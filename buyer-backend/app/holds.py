"""
Product holds.

When the agent decides stock is sufficient, the product is held for N days.
While held it is skipped completely by STN and LTN — no metrics, no LLM call.
"""

from __future__ import annotations

from datetime import date, timedelta


async def active_holds(pool, as_of: date) -> dict[int, dict]:
    """{product_id: hold_row} for holds that have not expired or been released."""
    rows = await pool.fetch("""
        SELECT product_id, hold_until, hold_days, reason, created_by, created_at
        FROM product_holds
        WHERE released_at IS NULL AND hold_until > $1
    """, as_of)
    return {r["product_id"]: dict(r) for r in rows}


async def set_hold(pool, product_id: int, hold_days: int, as_of: date,
                   reason: str = "", created_by: str = "stn") -> date:
    """Place (or replace) the hold on a product. Returns the hold_until date."""
    hold_days = max(1, min(365, int(hold_days)))
    hold_until = as_of + timedelta(days=hold_days)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("""
                UPDATE product_holds SET released_at = now()
                WHERE product_id = $1 AND released_at IS NULL
            """, product_id)
            await conn.execute("""
                INSERT INTO product_holds
                    (product_id, hold_days, hold_until, reason, created_by)
                VALUES ($1,$2,$3,$4,$5)
            """, product_id, hold_days, hold_until, reason[:500], created_by)
    return hold_until


async def release_hold(pool, product_id: int) -> bool:
    """Buyer override — put the product back in the next run."""
    result = await pool.execute("""
        UPDATE product_holds SET released_at = now()
        WHERE product_id = $1 AND released_at IS NULL
    """, product_id)
    return result.endswith("1")


async def list_holds(pool) -> list[dict]:
    rows = await pool.fetch("""
        SELECT h.product_id, p.name, h.hold_days, h.hold_until, h.reason,
               h.created_by, h.created_at
        FROM product_holds h JOIN products p USING (product_id)
        WHERE h.released_at IS NULL
        ORDER BY h.hold_until
    """)
    return [dict(r) for r in rows]
