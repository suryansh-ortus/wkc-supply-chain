"""
LTN — long-term agent. The original three-LLM chain, with Bright Data SERP
in place of Gemini's grounding search.

  LLM 1  reads the product and writes a search plan
  SERP   Bright Data runs those queries (in parallel, cached)
  LLM 2  reads the results and decides: action, quantity, hold, confidence

Unlike the original, it reads real sales history from Postgres — the old
version passed empty DataFrames, so the "long-term" agent had no long term.
"""

from __future__ import annotations

import json
from datetime import date

from app import config, holds, llm, metrics, search

SYSTEM = (
    f"You are a procurement strategist for a retail store in {config.LOCATION}. "
    "You plan purchases weeks ahead using weather, market prices, festivals and "
    "supply conditions — not just the store's own sales history."
)


# =============================================================================
# STAGE 1 — search plan
# =============================================================================

async def plan_searches(product: dict, m: dict, as_of: date) -> dict:
    prompt = f"""Plan the market research for a purchasing decision.

PRODUCT: {product['name']} ({product['category']})
LOCATION: {config.LOCATION}
DATE: {as_of:%d %B %Y}
SHELF LIFE: {product['shelf_life_days']} days
LEAD TIME: {product['lead_time_days']} days
SEASON: {m['seasonality'].get('seasonality_classification')}, currently \
{m['seasonality'].get('current_season_phase')}
CURRENT STOCK: {m['risk']['current_total_stock']} units, \
{m['risk']['days_until_stockout']} days of cover

Decide what outside information would change this decision, then write 4 search
queries that would find it. Think about weather, wholesale prices, festivals,
harvest or production cycles, and supply disruptions — whichever actually matter
for THIS product.

Return JSON:
{{"weather_dependency": "high|medium|low",
  "market_sensitivity": "high|medium|low",
  "key_factors": ["...", "..."],
  "search_queries": ["...", "...", "...", "..."]}}"""

    try:
        out = await llm.chat_json(prompt, system=SYSTEM, temperature=0.2, max_tokens=700)
        queries = [str(q) for q in (out.get("search_queries") or [])][:4]
        if not queries:
            raise ValueError("no queries")
        return {"ok": True, "queries": queries,
                "weather_dependency": out.get("weather_dependency"),
                "market_sensitivity": out.get("market_sensitivity"),
                "key_factors": out.get("key_factors") or []}
    except Exception as exc:
        # fall back to sensible queries so the run still produces something
        name = product["name"].split("(")[0].strip()
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "queries": [
                    f"{name} wholesale price {config.LOCATION} {as_of:%B %Y}",
                    f"{config.LOCATION} weather forecast next 30 days",
                    f"{name} demand festival season India {as_of.year}",
                    f"{name} supply shortage India news",
                ],
                "key_factors": []}


# =============================================================================
# STAGE 2 — gather (Bright Data)
# =============================================================================

async def gather(queries: list[str]) -> dict:
    try:
        results = await search.serp_many(queries, num=6)
        citations = [
            {"title": r["title"], "url": r["url"], "query": q}
            for q, rows in results.items() for r in rows[:3]
        ]
        return {"ok": True, "context": search.as_context(results),
                "citations": citations,
                "source_count": sum(len(v) for v in results.values())}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "context": "No market intelligence available.",
                "citations": [], "source_count": 0}


# =============================================================================
# STAGE 3 — decide
# =============================================================================

async def decide(product: dict, m: dict, plan: dict, intel: dict, as_of: date) -> dict:
    v, s, r = m["velocity"], m["seasonality"], m["risk"]

    prompt = f"""Decide the long-term purchase for ONE product. Today is {as_of:%d %B %Y}.

PRODUCT
  {product['name']} ({product['category']})
  cost Rs {product['cost_price']:,} / sells Rs {product['selling_price']:,}
  margin {m['profit']['profit_margin_percent']}%
  shelf life {product['shelf_life_days']} days, lead time {product['lead_time_days']} days

OUR OWN NUMBERS
  stock {r['current_total_stock']} units = {r['days_until_stockout']} days of cover
  selling {v.get('day_5_avg')}/day (5d), {v.get('day_30_avg')}/day (30d)
  season: {s.get('seasonality_classification')}, {s.get('current_season_phase')} phase,
  multiplier {s.get('demand_multiplier')}x, peak month {s.get('peak_months')}
  {s.get('reasoning')}

WHAT THE MARKET SAYS
{intel['context'][:6000]}

Weigh the outside information against our own numbers. If the market says prices
are about to rise or supply is about to tighten, buying earlier is worth it. If
demand is about to fall, buy less. If the search results say nothing useful about
this product, say so and rely on our numbers.

Never order more than can sell before it spoils: {product['shelf_life_days']} days
x {v.get('day_5_avg')}/day.

Return JSON:
{{"action": "order_now|order_later|reduce_order|no_order",
  "quantity": <integer>,
  "hold_days": <integer 1-365>,
  "confidence": <0-1>,
  "market_view": "<what the research actually told you, 1-2 sentences>",
  "reasoning": "<why this quantity, citing our numbers and the market>",
  "risk_factors": ["..."],
  "opportunities": ["..."]}}"""

    try:
        out = await llm.chat_json(prompt, system=SYSTEM, temperature=0.2, max_tokens=1200)
        qty = max(0, int(float(out.get("quantity", 0) or 0)))
        hold = max(1, min(365, int(float(out.get("hold_days", 30) or 30))))
        return {
            "ok": True,
            "action": str(out.get("action", "no_order")),
            "quantity": qty,
            "hold_days": hold,
            "confidence": max(0.0, min(1.0, float(out.get("confidence", 0.5) or 0.5))),
            "market_view": str(out.get("market_view", ""))[:1000],
            "reasoning": str(out.get("reasoning", ""))[:1500],
            "risk_factors": [str(x) for x in (out.get("risk_factors") or [])][:6],
            "opportunities": [str(x) for x in (out.get("opportunities") or [])][:6],
        }
    except Exception as exc:
        return {"ok": False, "action": "no_order", "quantity": 0, "hold_days": 7,
                "confidence": 0.0, "market_view": "",
                "reasoning": f"LLM unavailable ({type(exc).__name__}); no order placed.",
                "risk_factors": ["llm_failed"], "opportunities": []}


# =============================================================================
# RUN
# =============================================================================

async def analyse_product(product: dict, as_of: date, pool) -> dict:
    m = await metrics.compute_all(pool, product, as_of)
    plan = await plan_searches(product, m, as_of)
    intel = await gather(plan["queries"])
    decision = await decide(product, m, plan, intel, as_of)

    return {
        "product_id": product["product_id"],
        "product_name": product["name"],
        "recommended_quantity": decision["quantity"],
        "hold_days": decision["hold_days"],
        "confidence": decision["confidence"],
        "action": decision["action"],
        "market_view": decision["market_view"],
        "reasoning": decision["reasoning"],
        "risk_flags": decision["risk_factors"],
        "citations": intel["citations"],
        "queries": plan["queries"],
        "source_count": intel["source_count"],
        "metrics": m,
        "order_value": round(decision["quantity"] * float(product["cost_price"]), 2),
        "skipped": False,
    }


async def run(pool, as_of: date | None = None, triggered_by: str = "cli",
              on_progress=None, ignore_holds: bool = False) -> str:
    as_of = as_of or config.SIMULATED_TODAY

    run_id = await pool.fetchval("""
        INSERT INTO analysis_runs (engine, status, as_of_date, triggered_by, config)
        VALUES ('ltn', 'running', $1, $2, $3::jsonb) RETURNING run_id
    """, as_of, triggered_by, json.dumps({
        "model": config.DEEPINFRA_MODEL, "search": "brightdata_serp"}))

    try:
        products = await metrics.load_products(pool)
        held = {} if ignore_holds else await holds.active_holds(pool, as_of)

        for i, product in enumerate(products, start=1):
            pid = product["product_id"]

            if pid in held:
                if on_progress:
                    await on_progress(int(i / len(products) * 100), {
                        "product_id": pid, "product_name": product["name"],
                        "skipped": True, "hold": held[pid]})
                continue

            r = await analyse_product(product, as_of, pool)

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
                     recommended_quantity, hold_days, confidence, reasoning,
                     risk_flags, citations, status)
                VALUES ($1,$2,$3,$4::jsonb,$5,$6,$7,$8,$9,$10::jsonb,'pending_approval')
            """, run_id, pid, r["metrics"]["velocity"].get("optimal_order_quantity", 0),
                json.dumps(r["metrics"], default=str), r["recommended_quantity"],
                r["hold_days"], r["confidence"],
                (r["market_view"] + "\n\n" + r["reasoning"]).strip(),
                r["risk_flags"], json.dumps(r["citations"]))

            # one live recommendation per product — the newest wins
            await pool.execute("""
                UPDATE analysis_results SET status='superseded'
                 WHERE product_id = $1 AND status = 'pending_approval'
                   AND run_id <> $2
            """, pid, run_id)

            if r["hold_days"] > 0:
                await holds.set_hold(pool, pid, r["hold_days"], as_of,
                                     reason=r["reasoning"], created_by="ltn")

            await pool.execute(
                "UPDATE analysis_runs SET progress_pct=$2 WHERE run_id=$1",
                run_id, int(i / len(products) * 100))
            if on_progress:
                await on_progress(int(i / len(products) * 100), r)

        await pool.execute("""
            UPDATE analysis_runs SET status='completed', progress_pct=100,
                   finished_at=now() WHERE run_id=$1
        """, run_id)
        return str(run_id)

    except Exception as exc:
        await pool.execute("""
            UPDATE analysis_runs SET status='failed', error=$2, finished_at=now()
            WHERE run_id=$1
        """, run_id, f"{type(exc).__name__}: {exc}")
        raise
