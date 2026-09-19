"""
Daily stock check. No LLM, no agents — plain arithmetic, runs once a day.

The holds the agents place are predictions. This is the safety net underneath
them: if a product is genuinely about to run out, the hold is released and a
recommendation appears in the console. Nothing is ordered until the buyer
approves it — same gate as every other engine.

  runway = stock / daily velocity

  runway <= lead time            -> CRITICAL, recommend now, override any hold
  runway <= lead time + 3 days   -> URGENT,   recommend now, override any hold
  otherwise                      -> leave it alone

Perishables are capped at what sells within their shelf life, so a 3-day bread
order never becomes a 200-loaf order.
"""

from __future__ import annotations

from datetime import date

from app import config, holds, metrics, notify

URGENT_BUFFER_DAYS = 3


async def _open_run(pool, as_of, created_by: str) -> str:
    """One analysis_runs row per check, so its results show in the console."""
    return await pool.fetchval("""
        INSERT INTO analysis_runs (engine, status, as_of_date, triggered_by,
                                   progress_pct, finished_at)
        VALUES ('daily', 'completed', $1, $2, 100, now())
        RETURNING run_id
    """, as_of, created_by)


async def check(pool, as_of: date | None = None, place_orders: bool = True,
                created_by: str = "daily-check") -> dict:
    as_of = as_of or config.SIMULATED_TODAY
    products = await metrics.load_products(pool)
    active = await holds.active_holds(pool, as_of)

    findings = []
    run_id = None          # opened lazily, only if something actually needs ordering

    for product in products:
        pid = product["product_id"]
        daily_sales = await metrics.load_daily(pool, pid, as_of)
        vel = metrics.calculate_velocity_metrics(daily_sales)
        if "error" in vel:
            continue

        risk = metrics.calculate_risk(product, vel)
        runway = risk["days_until_stockout"]
        lead = product["lead_time_days"]
        rate = vel["day_5_avg"]

        if runway <= lead:
            level = "CRITICAL"
        elif runway <= lead + URGENT_BUFFER_DAYS:
            level = "URGENT"
        else:
            level = "OK"

        finding = {
            "product_id": pid,
            "product_name": product["name"],
            "stock": risk["current_total_stock"],
            "daily_rate": rate,
            "runway_days": runway,
            "lead_time_days": lead,
            "level": level,
            "was_held": pid in active,
            "hold_until": str(active[pid]["hold_until"]) if pid in active else None,
            "action": "none",
            "quantity": 0,
        }

        if level == "OK":
            findings.append(finding)
            continue

        # how much to buy: cover the lead time plus a fortnight, minus what we have
        target_days = lead + 14
        quantity = int(round(rate * target_days)) - risk["current_total_stock"]

        # perishables cannot be stockpiled
        if product["shelf_life_days"] < 30:
            cap = int(product["shelf_life_days"] * rate)
            quantity = min(quantity, cap)
            finding["shelf_life_cap"] = cap

        quantity = max(0, quantity)

        # same rupee ceiling the agents get
        capped = metrics.cap_by_value(quantity, product["cost_price"])
        if capped != quantity:
            finding["value_cap"] = capped
            quantity = capped

        finding["quantity"] = quantity

        if quantity <= 0:
            finding["action"] = "no_order_needed"
            findings.append(finding)
            continue

        if not place_orders:
            finding["action"] = "would_order"
            findings.append(finding)
            continue

        # A real shortage beats any hold the agents placed, so the product
        # stops being skipped. But nothing is ordered here — the buyer still
        # has to approve it, same as any other recommendation.
        if pid in active:
            await holds.release_hold(pool, pid)
            finding["hold_released"] = True

        try:
            run_id = run_id or await _open_run(pool, as_of, created_by)
            result_id = await pool.fetchval("""
                INSERT INTO analysis_results
                    (run_id, product_id, baseline_quantity, recommended_quantity,
                     confidence, reasoning, risk_flags, status)
                VALUES ($1,$2,$3,$4,1.0,$5,$6,'pending_approval')
                ON CONFLICT (run_id, product_id) DO NOTHING
                RETURNING result_id
            """, run_id, pid, quantity, quantity,
                ("Stock Watch: %s units left, selling %s/day — about %s days of "
                 "cover against a %s day lead time. Suggest ordering %s."
                 % (risk["current_total_stock"], rate, runway, lead, quantity)),
                ["stock_critical" if level == "CRITICAL" else "stock_urgent"])
            await pool.execute("""
                UPDATE analysis_results SET status='superseded'
                 WHERE product_id = $1 AND status = 'pending_approval'
                   AND run_id <> $2
            """, pid, run_id)
            finding["action"] = "awaiting_approval"
            finding["result_id"] = str(result_id) if result_id else None
        except Exception as exc:
            finding["action"] = "failed"
            finding["error"] = f"{type(exc).__name__}: {exc}"

        findings.append(finding)

    summary = {
        "as_of": str(as_of),
        "checked": len(findings),
        "critical": len([f for f in findings if f["level"] == "CRITICAL"]),
        "urgent": len([f for f in findings if f["level"] == "URGENT"]),
        "awaiting_approval": len([f for f in findings
                                  if f["action"] == "awaiting_approval"]),
        "holds_overridden": len([f for f in findings if f.get("hold_released")]),
        "findings": findings,
    }

    if summary["awaiting_approval"]:
        await notify.send("buyer", "daily_check.needs_approval", summary)

    return summary
