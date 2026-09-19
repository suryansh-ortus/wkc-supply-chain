"""
UPI payment for a purchase order.

No payment gateway. A UPI link is built from the PO amount, rendered as a QR,
and whoever is paying scans it with Paytm. Marking it paid is a human action —
UPI gives us no server-side confirmation.
"""

from __future__ import annotations

from urllib.parse import quote, urlencode

from fastapi import HTTPException

from app import config
from app.ws import hub


def build_upi_link(vpa: str, name: str, amount: float, note: str) -> str:
    return "upi://pay?" + urlencode({
        "pa": vpa, "pn": name, "am": "%.2f" % amount, "cu": "INR", "tn": note,
    })


def qr_url(upi_link: str, size: int = 320) -> str:
    return ("https://api.qrserver.com/v1/create-qr-code/?size=%dx%d&data=%s"
            % (size, size, quote(upi_link, safe="")))


async def get_or_create(pool, po_number: str) -> dict:
    """The payment row for a PO, created on first request."""
    po = await pool.fetchrow("""
        SELECT po.po_id, po.po_number, po.total_value, po.quantity, po.unit_price,
               po.seller_id, s.company_name, p.name AS product_name
        FROM purchase_orders po
        JOIN sellers s USING (seller_id)
        JOIN products p USING (product_id)
        WHERE po.po_number = $1
    """, po_number)
    if not po:
        raise HTTPException(404, "Purchase order not found")

    row = await pool.fetchrow("SELECT * FROM payments WHERE po_id = $1", po["po_id"])

    if row is None:
        amount = float(po["total_value"])
        note = po["po_number"]
        link = build_upi_link(config.UPI_VPA, config.UPI_PAYEE_NAME, amount, note)
        row = await pool.fetchrow("""
            INSERT INTO payments (po_id, vpa, payee_name, amount, upi_link)
            VALUES ($1,$2,$3,$4,$5)
            ON CONFLICT (po_id) DO UPDATE SET upi_link = EXCLUDED.upi_link
            RETURNING *
        """, po["po_id"], config.UPI_VPA, config.UPI_PAYEE_NAME, amount, link)

    return {"payment": dict(row), "po": dict(po)}


async def mark_paid(pool, po_number: str, by: str, utr: str = "") -> dict:
    data = await get_or_create(pool, po_number)
    payment, po = data["payment"], data["po"]

    await pool.execute("""
        UPDATE payments SET status='paid', paid_at=now(), paid_by=$2, utr=$3
        WHERE payment_id=$1 AND status='pending'
    """, payment["payment_id"], by, utr[:64])

    await pool.execute("""
        UPDATE purchase_orders SET status='confirmed' WHERE po_id=$1
    """, po["po_id"])

    event = {"po_number": po_number, "amount": float(payment["amount"]),
             "seller_id": po["seller_id"], "product_name": po["product_name"]}
    from app import notify
    await notify.send_many([f"seller:{po['seller_id']}", "buyer"], "payment.received", event)
    return event


def page_html(payment: dict, po: dict) -> str:
    """The page both portals link to. Public - it is meant to be scanned."""
    paid = payment["status"] == "paid"
    link = payment["upi_link"]

    body = ("""
      <div class="amt">Rs %s</div>
      <img src="%s" width="300" height="300">
      <p class="muted">Scan with Paytm, GPay or any UPI app</p>
      <a class="btn" href="%s">Open in UPI app</a>
      <form method="post" action="/pay/%s/paid" style="margin-top:26px">
        <input name="utr" placeholder="UPI reference number (optional)">
        <button type="submit">I have paid</button>
      </form>
    """ % ("{:,.2f}".format(float(payment["amount"])), qr_url(link), link,
           po["po_number"])) if not paid else ("""
      <div class="amt paid">Paid</div>
      <p class="muted">Rs %s received for this order</p>
      %s
    """ % ("{:,.2f}".format(float(payment["amount"])),
           ("<p class='muted'>UTR %s</p>" % payment["utr"]) if payment["utr"] else ""))

    return """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s - Rs %s</title>
<style>body{font:15px -apple-system,BlinkMacSystemFont,sans-serif;background:#f5f5f7;
color:#1d1d1f;text-align:center;padding:50px 20px;margin:0}
.card{background:#fff;max-width:440px;margin:0 auto;padding:32px;border-radius:16px;
box-shadow:0 2px 24px rgba(0,0,0,.08)}
.amt{font-size:38px;font-weight:600;margin:6px 0 20px}
.amt.paid{color:#1a8f5a}
.muted{color:#86868b;font-size:13px}
img{border-radius:10px}
input{width:100%%;padding:10px;border:1px solid #ddd;border-radius:8px;margin-bottom:10px;
font-size:14px;box-sizing:border-box}
a.btn,button{display:inline-block;background:#00b9f5;color:#fff;border:0;
padding:13px 30px;border-radius:9px;text-decoration:none;font-size:15px;cursor:pointer}
.head{font-size:13px;color:#86868b;line-height:1.7}</style></head>
<body><div class="card">
  <div class="head"><b>%s</b><br>%s &times; %s units<br>from %s</div>
  %s
</div></body></html>""" % (
        po["po_number"], "{:,.2f}".format(float(payment["amount"])),
        po["po_number"], po["product_name"], po["quantity"], po["company_name"],
        body)
