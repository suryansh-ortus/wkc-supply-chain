"""
THE AGENT — buyer service only.

Everything the store does automatically:
  * turn an approved recommendation into an RFQ and invite sellers
  * write the inquiry message to each seller
  * once all sellers reply, score them and open negotiations with the best
  * read each seller reply and decide: accept / counter / walk away
  * award the purchase order

The seller service never runs any of this. It calls in over /internal/*.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from app import config, llm
from app import memory, notify

# scoring weights from the original select_top_sellers_for_negotiation
W_PRICE, W_DELIVERY, W_RELIABILITY = 0.4, 0.3, 0.3


# =============================================================================
# 1. RECOMMENDATION -> RFQ   (the human-in-the-loop gate)
# =============================================================================

async def list_recommendations(pool, status: str = "pending_approval") -> list[dict]:
    rows = await pool.fetch("""
        SELECT r.result_id, r.product_id, p.name AS product_name, p.cost_price,
               r.recommended_quantity, r.approved_quantity, r.hold_days,
               r.reasoning, r.status, r.created_at,
               a.engine, a.as_of_date,
               (SELECT count(*) FROM seller_products sp
                 WHERE sp.product_id = r.product_id) AS seller_count
        FROM analysis_results r
        JOIN analysis_runs a USING (run_id)
        JOIN products p USING (product_id)
        WHERE ($1 = 'all' OR r.status::text = $1)
        ORDER BY r.created_at DESC, r.recommended_quantity DESC
    """, status)
    return [dict(r) for r in rows]


async def approve(pool, result_id: str, quantity: int | None, approved_by: str,
                  urgency: str = "normal") -> dict:
    """
    Buyer approves a recommendation -> RFQ goes out to every seller of that product.

    The partial unique index on procurement_requests.result_id means approving
    twice cannot create two RFQs.
    """
    result = await pool.fetchrow("""
        SELECT r.result_id, r.product_id, r.recommended_quantity, r.status,
               p.name AS product_name, p.lead_time_days
        FROM analysis_results r JOIN products p USING (product_id)
        WHERE r.result_id = $1
    """, result_id)
    if not result:
        raise HTTPException(404, "Recommendation not found")
    if result["status"] != "pending_approval":
        raise HTTPException(400, f"Already {result['status']}")

    qty = int(quantity if quantity is not None else result["recommended_quantity"])
    if qty <= 0:
        raise HTTPException(400, "Quantity must be greater than zero")

    deadline = datetime.now(timezone.utc) + timedelta(
        hours=24 if urgency in ("high", "critical") else 48)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("""
                UPDATE analysis_results
                SET status='approved', approved_quantity=$2, approved_by=$3,
                    approved_at=now()
                WHERE result_id=$1
            """, result_id, qty, approved_by)

            request_id = await conn.fetchval("""
                INSERT INTO procurement_requests
                    (product_id, result_id, quantity_needed, urgency, status,
                     response_deadline, created_by)
                VALUES ($1,$2,$3,$4::request_urgency,'collecting',$5,$6)
                RETURNING request_id
            """, result["product_id"], result_id, qty, urgency, deadline, approved_by)

            sellers = await conn.fetch("""
                SELECT s.seller_id, s.company_name, s.tier, c.contact_person, c.email
                FROM sellers s
                JOIN seller_products sp USING (seller_id)
                LEFT JOIN seller_contacts c ON c.seller_id = s.seller_id AND c.is_primary
                WHERE sp.product_id = $1 AND s.status = 'active'
            """, result["product_id"])

            if not sellers:
                raise HTTPException(400, "No active sellers for this product")

            invitations = []
            for seller in sellers:
                message = await write_inquiry(
                    seller["company_name"], seller["contact_person"] or "Sir/Madam",
                    result["product_name"], qty)
                invitation_id = await conn.fetchval("""
                    INSERT INTO rfq_invitations (request_id, seller_id, message)
                    VALUES ($1,$2,$3) RETURNING invitation_id
                """, request_id, seller["seller_id"], message)
                invitations.append({
                    "invitation_id": str(invitation_id),
                    "seller_id": seller["seller_id"],
                    "company_name": seller["company_name"],
                })

    # tell every invited seller, live
    for inv in invitations:
        await notify.send(f"seller:{inv['seller_id']}", "rfq.invited", {
            "invitation_id": inv["invitation_id"],
            "request_id": str(request_id),
            "product_name": result["product_name"],
            "quantity": qty,
            "deadline": deadline.isoformat(),
        })
    await notify.send("buyer", "rfq.created", {
        "request_id": str(request_id), "product_name": result["product_name"],
        "quantity": qty, "sellers_invited": len(invitations)})

    return {"request_id": str(request_id), "quantity": qty,
            "sellers_invited": len(invitations), "invitations": invitations}


async def create_rfq(pool, product_id: int, quantity: int, created_by: str,
                     urgency: str = "normal", note: str = "",
                     result_id: str | None = None) -> dict:
    """
    Raise an RFQ directly, with no recommendation behind it. Used by the daily
    stock check, which acts on arithmetic rather than on an agent's opinion.
    """
    product = await pool.fetchrow(
        "SELECT product_id, name FROM products WHERE product_id = $1", product_id)
    if not product:
        raise HTTPException(404, "Product not found")
    if quantity <= 0:
        raise HTTPException(400, "Quantity must be greater than zero")

    deadline = datetime.now(timezone.utc) + timedelta(
        hours=24 if urgency in ("high", "critical") else 48)

    async with pool.acquire() as conn:
        async with conn.transaction():
            request_id = await conn.fetchval("""
                INSERT INTO procurement_requests
                    (product_id, result_id, quantity_needed, urgency, status,
                     response_deadline, created_by, notes)
                VALUES ($1,$2,$3,$4::request_urgency,'collecting',$5,$6,$7)
                RETURNING request_id
            """, product_id, result_id, quantity, urgency, deadline, created_by,
                note[:500])

            sellers = await conn.fetch("""
                SELECT s.seller_id, s.company_name, c.contact_person
                FROM sellers s
                JOIN seller_products sp USING (seller_id)
                LEFT JOIN seller_contacts c ON c.seller_id = s.seller_id AND c.is_primary
                WHERE sp.product_id = $1 AND s.status = 'active'
            """, product_id)
            if not sellers:
                raise HTTPException(400, "No active sellers for this product")

            invitations = []
            for seller in sellers:
                message = await write_inquiry(
                    seller["company_name"], seller["contact_person"] or "Sir/Madam",
                    product["name"], quantity)
                invitation_id = await conn.fetchval("""
                    INSERT INTO rfq_invitations (request_id, seller_id, message)
                    VALUES ($1,$2,$3) RETURNING invitation_id
                """, request_id, seller["seller_id"], message)
                invitations.append({"invitation_id": str(invitation_id),
                                    "seller_id": seller["seller_id"],
                                    "company_name": seller["company_name"]})

    for inv in invitations:
        await notify.send(f"seller:{inv['seller_id']}", "rfq.invited", {
            "invitation_id": inv["invitation_id"], "request_id": str(request_id),
            "product_name": product["name"], "quantity": quantity,
            "urgency": urgency, "deadline": deadline.isoformat()})
    await notify.send("buyer", "rfq.created", {
        "request_id": str(request_id), "product_name": product["name"],
        "quantity": quantity, "sellers_invited": len(invitations),
        "automatic": result_id is None})

    return {"request_id": str(request_id), "quantity": quantity,
            "sellers_invited": len(invitations), "invitations": invitations}


async def reject(pool, result_id: str, note: str, by: str) -> dict:
    await pool.execute("""
        UPDATE analysis_results SET status='rejected', rejection_note=$2,
               approved_by=$3, approved_at=now()
        WHERE result_id=$1 AND status='pending_approval'
    """, result_id, note[:500], by)
    return {"result_id": result_id, "status": "rejected"}


async def write_inquiry(company: str, contact: str, product: str, qty: int) -> str:
    prompt = f"""Write a short business enquiry to a supplier.

From: a retail store in {config.LOCATION}
To: {contact} at {company}
We need: {qty} units of {product}

Two or three sentences. State the requirement, ask for their best price and
delivery time, mention this could become a regular order. Under 80 words.
Plain text only, no placeholders, no subject line."""
    try:
        return (await llm.chat(prompt, temperature=0.3, max_tokens=200)).strip()
    except Exception:
        return (f"Hello {contact}, we need {qty} units of {product} for our store in "
                f"{config.LOCATION}. Please share your best price and delivery time. "
                f"We are looking for a reliable supplier for regular orders.")


# =============================================================================
# 2. ALL QUOTES IN -> SCORE AND OPEN NEGOTIATIONS
# =============================================================================

async def score_quotes(pool, request_id: str) -> list[dict]:
    """Original scoring: price 0.4, delivery 0.3, reliability 0.3."""
    rows = await pool.fetch("""
        SELECT q.quote_id, q.seller_id, q.quoted_price, q.expected_delivery_days,
               s.company_name, s.reliability_score
        FROM quotes q JOIN sellers s USING (seller_id)
        WHERE q.request_id = $1 AND q.response = 'yes'
    """, request_id)
    quotes = [dict(r) for r in rows]
    if not quotes:
        return []

    prices = [float(q["quoted_price"]) for q in quotes]
    days = [q["expected_delivery_days"] for q in quotes]
    lo_p, hi_p, lo_d, hi_d = min(prices), max(prices), min(days), max(days)

    for q in quotes:
        price_score = (100 * (hi_p - float(q["quoted_price"])) / (hi_p - lo_p)
                       if hi_p > lo_p else 100)
        delivery_score = (100 * (hi_d - q["expected_delivery_days"]) / (hi_d - lo_d)
                          if hi_d > lo_d else 100)
        q["score"] = round(price_score * W_PRICE + delivery_score * W_DELIVERY
                           + float(q["reliability_score"]) * W_RELIABILITY, 2)
        await pool.execute("UPDATE quotes SET score=$2 WHERE quote_id=$1",
                           q["quote_id"], q["score"])

    quotes.sort(key=lambda q: q["score"], reverse=True)
    return quotes


async def open_negotiations(pool, request_id: str) -> list[dict]:
    """
    Called once, when the last seller has replied. The caller has already won the
    status race (collecting -> negotiating), so this cannot run twice.

    We negotiate with ONE seller: the cheapest quote. Simple and quick.
    """
    ranked = await score_quotes(pool, request_id)
    if not ranked:
        await pool.execute(
            "UPDATE procurement_requests SET status='cancelled' WHERE request_id=$1",
            request_id)
        await notify.send("buyer", "rfq.no_offers", {"request_id": str(request_id)})
        return []

    product = await pool.fetchrow("""
        SELECT p.product_id, p.name, p.cost_price, p.lead_time_days
        FROM procurement_requests r JOIN products p USING (product_id)
        WHERE r.request_id = $1
    """, request_id)

    # cheapest price wins the seat at the table
    winner = min(ranked, key=lambda q: float(q["quoted_price"]))

    started = []
    for quote in [winner]:
        opening = await write_opening_offer(quote, dict(product))

        negotiation_id = await pool.fetchval("""
            INSERT INTO negotiations
                (request_id, quote_id, seller_id, status, current_round, max_rounds,
                 opening_price, opening_delivery)
            VALUES ($1,$2,$3,'active',1,$4,$5,$6)
            RETURNING negotiation_id
        """, request_id, quote["quote_id"], quote["seller_id"],
            config.MAX_NEGOTIATION_ROUNDS, opening["price"], opening["delivery"])

        await pool.execute("""
            INSERT INTO negotiation_messages
                (negotiation_id, round, sender, body, proposed_price, proposed_delivery)
            VALUES ($1,1,'buyer',$2,$3,$4)
        """, negotiation_id, opening["message"], opening["price"], opening["delivery"])

        started.append({"negotiation_id": str(negotiation_id),
                        "seller_id": quote["seller_id"],
                        "company_name": quote["company_name"],
                        "score": quote["score"]})

        await notify.send(f"seller:{quote['seller_id']}", "negotiation.started", {
            "negotiation_id": str(negotiation_id),
            "product_name": product["name"],
            "message": opening["message"],
            "proposed_price": float(opening["price"]) if opening["price"] else None,
            "proposed_delivery": opening["delivery"],
        })

    await notify.send("buyer", "negotiations.opened", {
        "request_id": str(request_id), "count": len(started), "sellers": started})
    return started


async def write_opening_offer(quote: dict, product: dict) -> dict:
    """Target: 10% under their quote, or 5% under our cost price — whichever is lower."""
    current_price = float(quote["quoted_price"])
    target_price = min(current_price * 0.9, float(product["cost_price"]) * 0.95)
    target_delivery = max(quote["expected_delivery_days"] - 1, product["lead_time_days"])
    company = quote["company_name"]

    # What cognee remembers about this supplier from past deals. Empty on the
    # first ever negotiation, and empty if cognee is switched off.
    past = await memory.recall(
        f"What happened in past negotiations with {company}? "
        f"Did they lower their price, and by how much?")

    prompt = f"""Write a short negotiation opening message to a supplier.

Their offer: Rs {current_price:,.2f} per unit, {quote['expected_delivery_days']} days delivery
We want:     Rs {target_price:,.2f} per unit, {target_delivery} days delivery
Supplier:    {company}
{f"What we remember about them: {past}" if past else ""}

Thank them, propose our terms, mention regular repeat orders as the reason they
should move. Respectful but direct. Under 90 words. Plain text, no placeholders."""

    try:
        message = (await llm.chat(prompt, temperature=0.3, max_tokens=250)).strip()
    except Exception:
        message = (f"Thank you {company} for quoting Rs {current_price:,.2f}. "
                   f"For a long term arrangement with regular orders, could you do "
                   f"Rs {target_price:,.2f} with {target_delivery} day delivery?")

    return {"message": message, "price": round(target_price, 2),
            "delivery": target_delivery}


# =============================================================================
# 3. THE AGENT ANSWERS A SELLER
# =============================================================================

async def respond_to_seller(pool, negotiation_id: str) -> dict:
    """
    Read the negotiation so far and decide: accept, counter, or end.
    Called right after a seller posts a message.
    """
    neg = await pool.fetchrow("""
        SELECT n.*, s.company_name, p.name AS product_name, p.cost_price,
               p.lead_time_days, r.quantity_needed
        FROM negotiations n
        JOIN sellers s USING (seller_id)
        JOIN procurement_requests r USING (request_id)
        JOIN products p ON p.product_id = r.product_id
        WHERE n.negotiation_id = $1
    """, negotiation_id)
    if not neg:
        raise HTTPException(404, "Negotiation not found")
    if neg["status"] != "active":
        return {"action": "none", "reason": f"negotiation is {neg['status']}"}

    messages = await pool.fetch("""
        SELECT round, sender, body, proposed_price, proposed_delivery
        FROM negotiation_messages WHERE negotiation_id=$1 ORDER BY created_at
    """, negotiation_id)

    last_seller = next((m for m in reversed(messages) if m["sender"] == "seller"), None)
    history = "\n".join(
        f"Round {m['round']} {m['sender']}: {m['body']}"
        + (f" [price Rs {m['proposed_price']}]" if m["proposed_price"] else "")
        for m in messages)

    seller_price = (float(last_seller["proposed_price"])
                    if last_seller and last_seller["proposed_price"] else None)
    our_cost = float(neg["cost_price"])

    prompt = f"""You are negotiating a purchase for a retail store. Decide the next move.

PRODUCT: {neg['product_name']}, {neg['quantity_needed']} units
SUPPLIER: {neg['company_name']}
OUR REFERENCE COST: Rs {our_cost:,.2f} per unit
THEIR OPENING QUOTE: Rs {float(neg['opening_price'] or 0):,.2f}
ROUND {neg['current_round']} of {neg['max_rounds']}

CONVERSATION SO FAR
{history}

THEIR LATEST OFFER: {'Rs %.2f' % seller_price if seller_price else 'no price given'}, \
{last_seller['proposed_delivery'] if last_seller else '?'} days

DECIDE:
- accept  : you MUST accept if their price is at or below Rs {our_cost:,.2f}.
            Do not squeeze further - the deal is already good for us.
            Also accept on the last round if the offer is anywhere near reasonable.
- counter : only if their price is still ABOVE Rs {our_cost:,.2f} and rounds remain
- end     : they will not move and the price is far above Rs {our_cost:,.2f}

This is round {neg['current_round']} of {neg['max_rounds']}. On the last round you
must accept or end - no more counters. Close the deal quickly.

Return only JSON:
{{"action": "accept|counter|end",
  "counter_price": <number or null>,
  "counter_delivery": <integer or null>,
  "message": "<what to say to them, under 70 words>",
  "reason": "<one line, for our own records>"}}"""

    try:
        out = await llm.chat_json(prompt, temperature=0.2, max_tokens=500)
        action = str(out.get("action", "counter")).lower()
    except Exception as exc:
        out = {"message": "We will review this and come back to you.",
               "reason": f"LLM unavailable: {type(exc).__name__}"}
        action = "counter" if neg["current_round"] < neg["max_rounds"] else "end"

    if action not in ("accept", "counter", "end"):
        action = "counter"
    if action == "counter" and neg["current_round"] >= neg["max_rounds"]:
        action = "end"

    body = str(out.get("message", ""))[:2000] or "Thank you for your response."

    if action == "accept":
        final_price = seller_price or float(neg["opening_price"] or 0)
        final_delivery = (last_seller["proposed_delivery"] if last_seller else None) \
            or neg["lead_time_days"]
        await pool.execute("""
            UPDATE negotiations SET status='accepted', final_price=$2,
                   final_delivery=$3, version=version+1, updated_at=now()
            WHERE negotiation_id=$1
        """, negotiation_id, final_price, final_delivery)
        await _add_message(pool, negotiation_id, neg["current_round"], body)

    elif action == "counter":
        new_round = neg["current_round"] + 1
        await pool.execute("""
            UPDATE negotiations SET current_round=$2, version=version+1, updated_at=now()
            WHERE negotiation_id=$1
        """, negotiation_id, new_round)
        await _add_message(pool, negotiation_id, new_round, body,
                           out.get("counter_price"), out.get("counter_delivery"))

    else:
        await pool.execute("""
            UPDATE negotiations SET status='ended', version=version+1, updated_at=now()
            WHERE negotiation_id=$1
        """, negotiation_id)
        await _add_message(pool, negotiation_id, neg["current_round"], body)

    payload = {"negotiation_id": str(negotiation_id), "action": action,
               "message": body, "reason": out.get("reason", "")}
    await notify.send_many([f"negotiation:{negotiation_id}",
                         f"seller:{neg['seller_id']}", "buyer"],
                        "negotiation.buyer_replied", payload)
    return payload


async def _add_message(pool, negotiation_id, round_no, body, price=None, delivery=None):
    await pool.execute("""
        INSERT INTO negotiation_messages
            (negotiation_id, round, sender, body, proposed_price, proposed_delivery)
        VALUES ($1,$2,'buyer',$3,$4,$5)
        ON CONFLICT (negotiation_id, round, sender) DO NOTHING
    """, negotiation_id, round_no, body,
        float(price) if price else None, int(delivery) if delivery else None)


# =============================================================================
# 4. AWARD
# =============================================================================

async def award(pool, negotiation_id: str, by: str) -> dict:
    neg = await pool.fetchrow("""
        SELECT n.*, r.quantity_needed, r.product_id, p.name AS product_name,
               s.company_name, q.quoted_price
        FROM negotiations n
        JOIN procurement_requests r USING (request_id)
        JOIN products p ON p.product_id = r.product_id
        JOIN sellers s ON s.seller_id = n.seller_id
        JOIN quotes q ON q.quote_id = n.quote_id
        WHERE n.negotiation_id = $1
    """, negotiation_id)
    if not neg:
        raise HTTPException(404, "Negotiation not found")
    if neg["status"] != "accepted":
        raise HTTPException(400, "Only an accepted negotiation can be awarded")

    po_number = f"PO-{datetime.now():%Y%m%d}-{str(neg['request_id'])[:8]}"

    async with pool.acquire() as conn:
        async with conn.transaction():
            po_id = await conn.fetchval("""
                INSERT INTO purchase_orders
                    (po_number, request_id, negotiation_id, seller_id, product_id,
                     quantity, unit_price, delivery_days)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                ON CONFLICT (request_id) DO NOTHING
                RETURNING po_id
            """, po_number, neg["request_id"], negotiation_id, neg["seller_id"],
                neg["product_id"], neg["quantity_needed"], neg["final_price"],
                neg["final_delivery"] or 7)

            if po_id is None:
                raise HTTPException(400, "This request has already been awarded")

            await conn.execute("""
                UPDATE procurement_requests SET status='awarded', updated_at=now()
                WHERE request_id=$1
            """, neg["request_id"])
            await conn.execute("""
                UPDATE negotiations SET status='ended', updated_at=now()
                WHERE request_id=$1 AND negotiation_id <> $2 AND status='active'
            """, neg["request_id"], negotiation_id)

    data = {"po_id": str(po_id), "po_number": po_number,
            "seller_id": neg["seller_id"], "product_name": neg["product_name"],
            "quantity": neg["quantity_needed"],
            "unit_price": float(neg["final_price"]),
            "total": float(neg["final_price"]) * neg["quantity_needed"]}
    await notify.send_many([f"seller:{neg['seller_id']}", "buyer"], "po.issued", data)

    # Tell cognee how this one went, so the next negotiation with this
    # supplier starts with something instead of nothing.
    quoted = float(neg["quoted_price"])
    final = float(neg["final_price"])
    cut = round((quoted - final) / quoted * 100, 1) if quoted else 0.0
    memory.remember_later(
        f"{neg['company_name']} supplied {neg['product_name']} on {po_number}. "
        f"They first quoted Rs {quoted:,.2f} per unit and settled at "
        f"Rs {final:,.2f} after {neg['current_round']} rounds, a {cut}% "
        f"reduction, with delivery in {neg['final_delivery'] or 7} days.")

    return data


async def list_requests(pool) -> list[dict]:
    rows = await pool.fetch("""
        SELECT r.request_id, r.status, r.quantity_needed, r.urgency, r.created_at,
               r.response_deadline, p.name AS product_name, p.product_id,
               (SELECT count(*) FROM rfq_invitations i WHERE i.request_id=r.request_id)
                   AS invited,
               (SELECT count(*) FROM quotes q WHERE q.request_id=r.request_id)
                   AS replied,
               (SELECT count(*) FROM quotes q
                 WHERE q.request_id=r.request_id AND q.response='yes') AS accepted
        FROM procurement_requests r JOIN products p USING (product_id)
        ORDER BY r.created_at DESC
    """)
    return [dict(r) for r in rows]


async def request_detail(pool, request_id: str) -> dict:
    req = await pool.fetchrow("""
        SELECT r.*, p.name AS product_name, p.cost_price
        FROM procurement_requests r JOIN products p USING (product_id)
        WHERE r.request_id=$1
    """, request_id)
    if not req:
        raise HTTPException(404, "Request not found")

    quotes = await pool.fetch("""
        SELECT q.*, s.company_name, s.reliability_score, s.tier
        FROM quotes q JOIN sellers s USING (seller_id)
        WHERE q.request_id=$1 ORDER BY q.score DESC NULLS LAST
    """, request_id)

    negotiations = await pool.fetch("""
        SELECT n.negotiation_id, n.seller_id, s.company_name, n.status,
               n.current_round, n.max_rounds, n.opening_price, n.final_price
        FROM negotiations n JOIN sellers s USING (seller_id)
        WHERE n.request_id=$1
    """, request_id)

    po = await pool.fetchrow("""
        SELECT po_number, total_value, status FROM purchase_orders WHERE request_id = $1
    """, request_id)

    request = dict(req)
    if po:
        request["po_number"] = po["po_number"]
        request["po_total"] = float(po["total_value"])
        request["po_status"] = po["status"]

    return {"request": request,
            "quotes": [dict(q) for q in quotes],
            "negotiations": [dict(n) for n in negotiations]}
