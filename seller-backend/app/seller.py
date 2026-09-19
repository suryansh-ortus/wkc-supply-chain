"""
SELLER SIDE — what a supplier can do.

Only four things:
  * see the requests sent to them
  * answer one (yes with a price, or no)
  * see their negotiations
  * write a message in a negotiation

Nothing here decides anything. The moment a seller acts, this service calls the
buyer service, and the agent takes over there.
"""

from __future__ import annotations

from fastapi import HTTPException

from app import config, notify


# =============================================================================
# PROFILE
# =============================================================================

async def demo_accounts(pool) -> list[dict]:
    """
    Public list for the login dropdown. Demo convenience, nothing sensitive.

    Narrowed to PORTAL_PRODUCTS so the dropdown shows the suppliers for the
    product line this portal is demoing, not all eighteen in the database.
    """
    if config.PORTAL_PRODUCTS:
        rows = await pool.fetch("""
            SELECT DISTINCT u.seller_id, u.email, s.company_name,
                   s.location, s.tier::text
            FROM seller_users u
            JOIN sellers s USING (seller_id)
            JOIN seller_products sp ON sp.seller_id = s.seller_id
            WHERE s.status = 'active' AND sp.product_id = ANY($1::int[])
            ORDER BY s.company_name
        """, config.PORTAL_PRODUCTS)
    else:
        rows = await pool.fetch("""
            SELECT u.seller_id, u.email, s.company_name, s.location, s.tier::text
            FROM seller_users u JOIN sellers s USING (seller_id)
            WHERE s.status = 'active'
            ORDER BY s.company_name
        """)
    return [dict(r) for r in rows]


async def profile(pool, seller_id: str) -> dict:
    row = await pool.fetchrow("""
        SELECT s.*, c.contact_person, c.email, c.phone
        FROM sellers s
        LEFT JOIN seller_contacts c ON c.seller_id = s.seller_id AND c.is_primary
        WHERE s.seller_id = $1
    """, seller_id)
    if not row:
        raise HTTPException(404, "Seller not found")

    products = await pool.fetch("""
        SELECT p.product_id, p.name FROM seller_products sp
        JOIN products p USING (product_id) WHERE sp.seller_id = $1
    """, seller_id)

    stats = await pool.fetchrow("""
        SELECT
          (SELECT count(*) FROM rfq_invitations WHERE seller_id=$1)          AS invited,
          (SELECT count(*) FROM quotes WHERE seller_id=$1)                   AS quoted,
          (SELECT count(*) FROM quotes WHERE seller_id=$1 AND response='yes') AS accepted,
          (SELECT count(*) FROM negotiations WHERE seller_id=$1 AND status='active')
                                                                             AS negotiating,
          (SELECT count(*) FROM purchase_orders WHERE seller_id=$1)          AS orders_won
    """, seller_id)

    return {"seller": dict(row),
            "products": [dict(p) for p in products],
            "stats": dict(stats)}


# =============================================================================
# REQUESTS
# =============================================================================

async def open_requests(pool, seller_id: str) -> list[dict]:
    """Invitations this seller has not answered yet."""
    rows = await pool.fetch("""
        SELECT i.invitation_id, i.request_id, i.message, i.sent_at, i.status,
               r.quantity_needed, r.urgency, r.response_deadline,
               p.product_id, p.name AS product_name
        FROM rfq_invitations i
        JOIN procurement_requests r USING (request_id)
        JOIN products p ON p.product_id = r.product_id
        LEFT JOIN quotes q ON q.invitation_id = i.invitation_id
        WHERE i.seller_id = $1 AND q.quote_id IS NULL
          AND r.status IN ('collecting','negotiating')
        ORDER BY i.sent_at DESC
    """, seller_id)
    return [dict(r) for r in rows]


async def history(pool, seller_id: str) -> list[dict]:
    rows = await pool.fetch("""
        SELECT q.quote_id, q.response, q.quoted_price, q.expected_delivery_days,
               q.notes, q.score, q.submitted_at,
               p.name AS product_name, r.quantity_needed, r.status AS request_status,
               n.status AS negotiation_status, n.final_price,
               po.po_number, po.total_value
        FROM quotes q
        JOIN procurement_requests r USING (request_id)
        JOIN products p ON p.product_id = r.product_id
        LEFT JOIN negotiations n ON n.quote_id = q.quote_id
        LEFT JOIN purchase_orders po ON po.negotiation_id = n.negotiation_id
        WHERE q.seller_id = $1
        ORDER BY q.submitted_at DESC
    """, seller_id)
    return [dict(r) for r in rows]


async def mark_viewed(pool, seller_id: str, invitation_id: str) -> None:
    await pool.execute("""
        UPDATE rfq_invitations SET status='viewed', viewed_at=now()
        WHERE invitation_id=$1 AND seller_id=$2 AND status='sent'
    """, invitation_id, seller_id)


# =============================================================================
# SUBMIT A QUOTE
# =============================================================================

async def submit_quote(pool, seller_id: str, invitation_id: str, response: str,
                       price: float | None, delivery_days: int | None,
                       notes: str = "") -> dict:
    """
    Answer an RFQ. UNIQUE(invitation_id) on quotes makes a double submit impossible,
    so a double-clicked button cannot create two quotes.
    """
    response = (response or "").lower()
    if response not in ("yes", "no"):
        raise HTTPException(400, "Response must be yes or no")

    if response == "yes":
        if not price or not delivery_days:
            raise HTTPException(400, "Price and delivery days are required to quote")
        if price <= 0 or delivery_days <= 0:
            raise HTTPException(400, "Price and delivery days must be positive")
    else:
        price, delivery_days = None, None

    invitation = await pool.fetchrow("""
        SELECT i.invitation_id, i.request_id, i.seller_id, r.status AS request_status,
               p.name AS product_name
        FROM rfq_invitations i
        JOIN procurement_requests r USING (request_id)
        JOIN products p ON p.product_id = r.product_id
        WHERE i.invitation_id = $1
    """, invitation_id)

    if not invitation:
        raise HTTPException(404, "Request not found")
    if invitation["seller_id"] != seller_id:
        raise HTTPException(403, "That request is not yours")
    if invitation["request_status"] != "collecting":
        raise HTTPException(400, "This request is no longer accepting quotes")

    try:
        quote_id = await pool.fetchval("""
            INSERT INTO quotes (invitation_id, request_id, seller_id, response,
                                quoted_price, expected_delivery_days, notes)
            VALUES ($1,$2,$3,$4::quote_response,$5,$6,$7)
            RETURNING quote_id
        """, invitation_id, invitation["request_id"], seller_id, response,
            price, delivery_days, notes[:1000])
    except Exception as exc:
        if "uq" in str(exc) or "unique" in str(exc).lower():
            raise HTTPException(400, "You have already answered this request")
        raise

    await pool.execute("""
        UPDATE rfq_invitations SET status='responded' WHERE invitation_id=$1
    """, invitation_id)

    await notify.send("buyer", "quote.submitted", {
        "request_id": str(invitation["request_id"]), "seller_id": seller_id,
        "response": response, "quoted_price": price,
        "product_name": invitation["product_name"]})

    negotiations_started = await _maybe_close_collection(pool, invitation["request_id"])

    return {"quote_id": str(quote_id), "response": response,
            "negotiations_started": negotiations_started}


async def _maybe_close_collection(pool, request_id) -> bool:
    """
    If every invited seller has answered, move the request from 'collecting' to
    'negotiating' and open negotiations.

    The UPDATE ... WHERE status='collecting' is the race guard: when two sellers
    submit at the same moment, only one transaction gets a row back, so
    negotiations are opened exactly once.
    """
    counts = await pool.fetchrow("""
        SELECT (SELECT count(*) FROM rfq_invitations WHERE request_id=$1) AS invited,
               (SELECT count(*) FROM quotes WHERE request_id=$1)          AS replied
    """, request_id)

    if counts["replied"] < counts["invited"]:
        return False

    won = await pool.fetchval("""
        UPDATE procurement_requests SET status='negotiating', updated_at=now()
        WHERE request_id=$1 AND status='collecting'
        RETURNING request_id
    """, request_id)

    if won is None:
        return False          # another request handled it

    # the agent lives in the buyer service - tell it to take over
    await notify.call_agent("quotes-complete", {"request_id": str(request_id)})
    return True


# =============================================================================
# NEGOTIATION
# =============================================================================

async def negotiations(pool, seller_id: str) -> list[dict]:
    rows = await pool.fetch("""
        SELECT n.negotiation_id, n.status, n.current_round, n.max_rounds,
               n.opening_price, n.final_price, n.updated_at,
               p.name AS product_name, r.quantity_needed,
               (SELECT body FROM negotiation_messages m
                 WHERE m.negotiation_id = n.negotiation_id
                 ORDER BY m.created_at DESC LIMIT 1) AS last_message,
               (SELECT sender::text FROM negotiation_messages m
                 WHERE m.negotiation_id = n.negotiation_id
                 ORDER BY m.created_at DESC LIMIT 1) AS last_sender
        FROM negotiations n
        JOIN procurement_requests r USING (request_id)
        JOIN products p ON p.product_id = r.product_id
        WHERE n.seller_id = $1
        ORDER BY n.updated_at DESC
    """, seller_id)
    return [dict(r) for r in rows]


async def negotiation_detail(pool, seller_id: str, negotiation_id: str) -> dict:
    neg = await pool.fetchrow("""
        SELECT n.*, p.name AS product_name, r.quantity_needed
        FROM negotiations n
        JOIN procurement_requests r USING (request_id)
        JOIN products p ON p.product_id = r.product_id
        WHERE n.negotiation_id = $1
    """, negotiation_id)
    if not neg:
        raise HTTPException(404, "Negotiation not found")
    if neg["seller_id"] != seller_id:
        raise HTTPException(403, "That negotiation is not yours")

    messages = await pool.fetch("""
        SELECT round, sender::text, body, proposed_price, proposed_delivery, created_at
        FROM negotiation_messages WHERE negotiation_id=$1 ORDER BY created_at
    """, negotiation_id)

    return {"negotiation": dict(neg), "messages": [dict(m) for m in messages]}


async def send_message(pool, seller_id: str, negotiation_id: str, message: str,
                       counter_price: float | None = None,
                       counter_delivery: int | None = None) -> dict:
    """Seller writes back. Our agent answers immediately."""
    if not message or not message.strip():
        raise HTTPException(400, "Message cannot be empty")

    neg = await pool.fetchrow("""
        SELECT negotiation_id, seller_id, status, current_round
        FROM negotiations WHERE negotiation_id = $1
    """, negotiation_id)
    if not neg:
        raise HTTPException(404, "Negotiation not found")
    if neg["seller_id"] != seller_id:
        raise HTTPException(403, "That negotiation is not yours")
    if neg["status"] != "active":
        raise HTTPException(400, f"This negotiation is {neg['status']}")

    # UNIQUE (negotiation_id, round, sender) stops a double click adding two turns
    inserted = await pool.fetchval("""
        INSERT INTO negotiation_messages
            (negotiation_id, round, sender, body, proposed_price, proposed_delivery)
        VALUES ($1,$2,'seller',$3,$4,$5)
        ON CONFLICT (negotiation_id, round, sender) DO NOTHING
        RETURNING message_id
    """, negotiation_id, neg["current_round"], message.strip()[:2000],
        counter_price, counter_delivery)

    if inserted is None:
        raise HTTPException(400, "You have already replied in this round")

    await notify.send_many([f"negotiation:{negotiation_id}", "buyer"],
                        "negotiation.seller_replied",
                        {"negotiation_id": str(negotiation_id),
                         "seller_id": seller_id, "message": message,
                         "proposed_price": counter_price,
                         "proposed_delivery": counter_delivery})

    # the agent answers in the buyer service
    reply = await notify.call_agent("seller-replied",
                                    {"negotiation_id": str(negotiation_id)})
    return {"sent": True, "buyer_reply": reply}
