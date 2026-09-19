"""
Bright Data SERP client (replaces Gemini's grounding search).

    results = await serp("mango wholesale price Mumbai July 2025")
    # -> [{"title": ..., "url": ..., "snippet": ...}, ...]

Results are cached in market_intelligence (table) so repeated runs don't burn
credits. Cache TTL: SERP_CACHE_TTL_HOURS.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import httpx

from app import config
from app.db import get_pool

BRIGHTDATA_ENDPOINT = "https://api.brightdata.com/request"


async def serp(query: str, num: int = 10, use_cache: bool = True) -> list[dict]:
    """Google results for `query`. Returns [] if the search fails."""
    key = hashlib.sha256(f"brightdata|{query}|{num}".encode()).hexdigest()

    if use_cache:
        cached = await _cache_get(key)
        if cached is not None:
            return cached

    if not config.BRIGHTDATA_API_KEY or not config.BRIGHTDATA_SERP_ZONE:
        raise RuntimeError(
            "BRIGHTDATA_API_KEY / BRIGHTDATA_SERP_ZONE are not set in backend/.env"
        )

    # brd_json=1 makes Bright Data return parsed SERP JSON instead of HTML.
    target = f"https://www.google.com/search?q={quote_plus(query)}&num={num}&brd_json=1"

    async with httpx.AsyncClient(timeout=60.0) as http:
        resp = await http.post(
            BRIGHTDATA_ENDPOINT,
            headers={"Authorization": f"Bearer {config.BRIGHTDATA_API_KEY}"},
            json={"zone": config.BRIGHTDATA_SERP_ZONE, "url": target, "format": "raw"},
        )
        resp.raise_for_status()
        body = resp.text

    results = _parse(body, num)
    if results:
        await _cache_put(key, query, results)
    return results


async def serp_many(queries: list[str], num: int = 8) -> dict[str, list[dict]]:
    """Run several queries at once. A single SERP call takes ~60s, so this
    matters: 4 queries in parallel is ~60s instead of ~4 minutes."""
    async def one(q: str) -> list[dict]:
        try:
            return await serp(q, num=num)
        except Exception as exc:                       # one bad query != dead pipeline
            print(f"  serp failed for {q!r}: {exc}")
            return []

    results = await asyncio.gather(*(one(q) for q in queries))
    return dict(zip(queries, results))


def as_context(results_by_query: dict[str, list[dict]], per_query: int = 5) -> str:
    """Flatten SERP results into a text block to paste into an LLM prompt."""
    lines: list[str] = []
    for query, results in results_by_query.items():
        if not results:
            continue
        lines.append(f"\n### Search: {query}")
        for r in results[:per_query]:
            lines.append(f"- {r['title']}\n  {r['snippet']}\n  source: {r['url']}")
    return "\n".join(lines) if lines else "No search results available."


def _parse(body: str, num: int) -> list[dict]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return []

    organic = data.get("organic") or data.get("organic_results") or []
    results = []
    for item in organic[:num]:
        url = item.get("link") or item.get("url") or ""
        if not url:
            continue
        results.append({
            "title": (item.get("title") or "").strip(),
            "url": url,
            "snippet": (item.get("description") or item.get("snippet") or "").strip(),
        })
    return results


# --- cache -------------------------------------------------------------------

async def _cache_get(key: str) -> list[dict] | None:
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT payload FROM market_intelligence "
        "WHERE cache_key = $1 AND expires_at > now()", key)
    if row is None:
        return None
    payload = row["payload"]
    return json.loads(payload) if isinstance(payload, str) else payload


async def _cache_put(key: str, query: str, results: list[dict]) -> None:
    pool = await get_pool()
    expires = datetime.now(timezone.utc) + timedelta(hours=config.SERP_CACHE_TTL_HOURS)
    await pool.execute("""
        INSERT INTO market_intelligence (cache_key, query, provider, payload, expires_at)
        VALUES ($1, $2, 'brightdata_serp', $3::jsonb, $4)
        ON CONFLICT (cache_key) DO UPDATE SET
            payload = EXCLUDED.payload,
            fetched_at = now(),
            expires_at = EXCLUDED.expires_at
    """, key, query, json.dumps(results), expires)
