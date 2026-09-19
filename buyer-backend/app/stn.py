"""
STN — short-term replenishment agent.

The analysis logic (profit / velocity / seasonality / risk) is the original,
untouched, in metrics.py. What changed here is the DECISION step:

  * the model returns JSON, not prose that gets scraped for digits
  * it must follow explicit ordering rules
  * it can decide to order nothing and HOLD the product for N days

A held product is skipped entirely on the next run — no metrics, no LLM call —
until the hold expires or a buyer releases it.
"""

from __future__ import annotations

import json
from datetime import date

from app import config, holds, llm, metrics

SYSTEM = (
    "You are the inventory planner for a single retail store in "
    f"{config.LOCATION}. You decide purchase orders for one product at a time. "
    "You do not over-buy. Unsold stock is dead capital, and out-of-season stock "
    "sits for months. You would rather hold and re-check than order early."
)


def build_prompt(p: dict, profit: dict, vel: dict, seas: dict, risk: dict,
                 as_of: date) -> str:
    daily = vel.get("day_5_avg", 0) or 0.0
    cover_needed = p["lead_time_days"] + 14
    mult = seas.get("demand_multiplier", 1.0)
    target = daily * mult * cover_needed
    shortfall = target - risk["current_total_stock"]

    perishable = p["shelf_life_days"] < 30
    shelf_cap = int(p["shelf_life_days"] * daily) if perishable else None

    if perishable:
        cap_text = (
            f"STEP 2 — SHELF LIFE CEILING (this product spoils in "
            f"{p['shelf_life_days']} days)\n"
            f"  Whatever Step 1 produced, it CANNOT exceed "
            f"{p['shelf_life_days']} days x {daily} units/day = {shelf_cap} units.\n"
            f"  This is a hard ceiling, not a suggestion. Stock beyond {shelf_cap} units "
            f"will be\n  thrown away unsold. If Step 1 gave a bigger number, use "
            f"{shelf_cap} instead.\n"
            f"  Order little and often for this product."
        )
    else:
        cap_text = ("STEP 2 — SHELF LIFE CEILING\n"
                    "  No ceiling: this product does not spoil.")

    if float(p["cost_price"]) > 5000:
        rule4 = (f"RULE 4 — EXPENSIVE STOCK\n"
                 f"  At Rs {p['cost_price']:,.0f} per unit this ties up serious capital, so "
                 f"buy only\n  what covers lead time + 14 days:\n"
                 f"  ({daily}/day x {mult} seasonal x {cover_needed} days) = {target:.1f} needed, "
                 f"minus {risk['current_total_stock']} in stock = {shortfall:.1f} units.")
    else:
        rule4 = ("RULE 4 — EXPENSIVE STOCK\n"
                 "  Does not apply: this is a low cost item.")

    return f"""Decide today's purchase order for ONE product. Today is {as_of:%d %B %Y}.

PRODUCT
  {p['product_name']} ({p['category']})
  cost Rs {p['cost_price']:,} per unit, sells for Rs {p['selling_price']:,}
  margin {profit['profit_margin_percent']}% ({profit['profitability_status']})
  shelf life {p['shelf_life_days']} days
  supplier lead time {p['lead_time_days']} days

STOCK RIGHT NOW
  back stock {p['quantity_in_inventory']} + shelf {p['current_quantity_in_store']} \
= {risk['current_total_stock']} units
  selling {vel.get('day_5_avg')} units/day (last 5 days), \
{vel.get('day_30_avg')} units/day (last 30 days)
  that is {risk['days_until_stockout']} days of stock left
  stockout risk: {risk['risk_level']}

SEASON
  {seas.get('seasonality_classification')} product, currently in \
{seas.get('current_season_phase')} phase
  seasonal demand multiplier {seas.get('demand_multiplier')}x \
(confidence {seas.get('season_confidence')}%)
  peak month(s) {seas.get('peak_months')}, weakest month(s) {seas.get('low_months')}
  {seas.get('reasoning')}

STEP 1 — PICK THE QUANTITY. Stop at the first rule that applies:

RULE 1 — DEAD SEASON
  If the phase is Off-Season or Declining AND current stock lasts longer than the
  rest of the season, order 0 and HOLD until the season is close again.
  Example: a winter product in July with 300 days of stock -> order 0, hold ~90 days.

RULE 2 — ALREADY COVERED
  If {risk['days_until_stockout']} days of stock is more than {cover_needed} days
  (lead time {p['lead_time_days']} + 14 days buffer), order 0 and HOLD for roughly
  ({risk['days_until_stockout']} - {p['lead_time_days']}) days.

{rule4}

RULE 5 — NORMAL REPLENISHMENT
  Order ({daily}/day x {mult} seasonal x {cover_needed} days) = {target:.1f} needed,
  minus {risk['current_total_stock']} already in stock = {shortfall:.1f} units.
  If that is zero or negative, order 0 and hold.

{cap_text}

STEP 3 — HOLD PERIOD (always required, whether you order or not)
  hold_days is how many days this product should be LEFT ALONE before it is looked
  at again. While held it is skipped completely. This stops the same order being
  placed again tomorrow.

  If you ORDERED something:
    the order plus current stock covers
    ({risk['current_total_stock']} + your order) / {daily if daily else 1}/day = some number of days.
    Set hold_days to that, minus the {p['lead_time_days']} day lead time, so the next
    review happens with just enough time to reorder before running out.

  If you ordered 0:
    set hold_days to how long the current stock lasts, or how long until the season
    turns — whichever comes first.

  Between 1 and 365.

Do the arithmetic before answering. Return only this JSON:
{{"order_quantity": <integer>,
  "hold_days": <integer>,
  "rule_applied": <1-5>,
  "reasoning": "<one or two sentences with the numbers you used>"}}"""


async def get_decision(product_data, profit, vel, seas, risk, as_of) -> dict:
    """JSON decision. On failure: order nothing, short hold, so nothing is bought blind."""
    try:
        out = await llm.chat_json(
            build_prompt(product_data, profit, vel, seas, risk, as_of),
            system=SYSTEM, temperature=0.1, max_tokens=700)

        qty = max(0, int(float(out.get("order_quantity", 0) or 0)))
        # every product gets a hold, ordered or not, so the same order is not
        # placed again on the next run
        hold = max(1, min(365, int(float(out.get("hold_days", 1) or 1))))
        return {
            "order_quantity": qty,
            "hold_days": hold,
            "rule_applied": out.get("rule_applied"),
            "reasoning": str(out.get("reasoning", ""))[:1000],
        }
    except Exception as exc:
        print(f"   LLM decision failed: {type(exc).__name__}: {exc}")
        return {"order_quantity": 0, "hold_days": 1, "rule_applied": None,
                "reasoning": f"LLM unavailable ({type(exc).__name__}); no order placed."}


async def analyse_product(product: dict, as_of: date, pool) -> dict:
    m = await metrics.compute_all(pool, product, as_of)

    product_data = {
        "product_id": product["product_id"],
        "product_name": product["name"],
        "category": product["category"],
        "quantity_in_inventory": product["warehouse_qty"],
        "current_quantity_in_store": product["store_qty"],
        "shelf_life_days": product["shelf_life_days"],
        "lead_time_days": product["lead_time_days"],
        "cost_price": float(product["cost_price"]),
        "selling_price": float(product["selling_price"]),
    }

    decision = await get_decision(product_data, m["profit"], m["velocity"],
                                  m["seasonality"], m["risk"], as_of)

    return {
        "product_id": product["product_id"],
        "product_name": product["name"],
        "recommended_quantity": decision["order_quantity"],
        "hold_days": decision["hold_days"],
        "rule_applied": decision["rule_applied"],
        "reasoning": decision["reasoning"],
        "optimal_order_quantity": m["velocity"].get("optimal_order_quantity", 0),
        "metrics": m,
        "order_value": round(decision["order_quantity"] * float(product["cost_price"]), 2),
        "skipped": False,
    }


async def run(pool, as_of: date | None = None, triggered_by: str = "cli",
              on_progress=None, ignore_holds: bool = False) -> str:
    as_of = as_of or config.SIMULATED_TODAY

    run_id = await pool.fetchval("""
        INSERT INTO analysis_runs (engine, status, as_of_date, triggered_by, config)
        VALUES ('stn', 'running', $1, $2, $3::jsonb) RETURNING run_id
    """, as_of, triggered_by, json.dumps({"model": config.DEEPINFRA_MODEL}))

    try:
        products = await metrics.load_products(pool)
        held = {} if ignore_holds else await holds.active_holds(pool, as_of)

        for i, product in enumerate(products, start=1):
            pid = product["product_id"]

            # held products never reach the LLM
            if pid in held:
                if on_progress:
                    await on_progress(int(i / len(products) * 100), {
                        "product_id": pid, "product_name": product["name"],
                        "skipped": True, "hold": held[pid]})
                continue

            r = await analyse_product(product, as_of, pool)

            # a ceiling in rupees, applied after the LLM has had its say
            capped = metrics.cap_by_value(r["recommended_quantity"],
                                          product["cost_price"])
            if capped != r["recommended_quantity"]:
                r["reasoning"] += (" Capped at %s units to stay within the "
                                   "Rs %s per-order limit."
                                   % (capped, f"{config.MAX_ORDER_VALUE:,}"))
                r["recommended_quantity"] = capped

            await pool.execute("""
                INSERT INTO analysis_results
                    (run_id, product_id, baseline_quantity, metrics,
                     recommended_quantity, hold_days, reasoning, status)
                VALUES ($1,$2,$3,$4::jsonb,$5,$6,$7,'pending_approval')
            """, run_id, pid, r["optimal_order_quantity"],
                json.dumps(r["metrics"], default=str), r["recommended_quantity"],
                r["hold_days"], r["reasoning"])

            # one live recommendation per product — the newest wins
            await pool.execute("""
                UPDATE analysis_results SET status='superseded'
                 WHERE product_id = $1 AND status = 'pending_approval'
                   AND run_id <> $2
            """, pid, run_id)

            if r["hold_days"] > 0:
                await holds.set_hold(pool, pid, r["hold_days"], as_of,
                                     reason=r["reasoning"], created_by="stn")

            await pool.execute(
                "UPDATE analysis_runs SET progress_pct = $2 WHERE run_id = $1",
                run_id, int(i / len(products) * 100))
            if on_progress:
                await on_progress(int(i / len(products) * 100), r)

        await pool.execute("""
            UPDATE analysis_runs SET status='completed', progress_pct=100,
                   finished_at=now() WHERE run_id = $1
        """, run_id)
        return str(run_id)

    except Exception as exc:
        await pool.execute("""
            UPDATE analysis_runs SET status='failed', error=$2, finished_at=now()
            WHERE run_id = $1
        """, run_id, f"{type(exc).__name__}: {exc}")
        raise
