"""
Cognee Cloud — the negotiation agent's memory, over HTTP.

Nothing runs locally. No cognee package, no graph database on disk, no
embedding model to download. Three plain REST calls against the hosted
tenant, so this deploys anywhere that can make an outbound request.

  remember(text)     POST /api/v1/add      then  POST /api/v1/cognify
  recall(question)   POST /api/v1/search

Used in exactly two places in agent.py:

  award()               -> remember how the deal went
  write_opening_offer() -> recall what this supplier did last time

Auth is the X-Api-Key header. Both settings come from .env:

  COGNEE_API_URL=https://your-tenant.aws.cognee.ai
  COGNEE_API_KEY=...

Leave either blank and the agent carries on exactly as it does today.

Docs: https://docs.cognee.ai/api-reference/introduction
"""

from __future__ import annotations

import asyncio

import httpx

from app import config

DATASET = "wkc_negotiations"


def enabled() -> bool:
    return bool(config.COGNEE_API_KEY and config.COGNEE_API_URL)


def _headers() -> dict:
    return {"X-Api-Key": config.COGNEE_API_KEY}


def _url(path: str) -> str:
    return config.COGNEE_API_URL.rstrip("/") + path


# =============================================================================
# WRITE
# =============================================================================

async def remember(text: str) -> bool:
    """Store one fact and ask cognee to fold it into the graph."""
    if not enabled():
        return False
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            # 1. hand over the sentence
            add = await client.post(
                _url("/api/v1/add"),
                headers=_headers(),
                files={"data": ("note.txt", text.encode(), "text/plain")},
                data={"datasetName": DATASET},
            )
            add.raise_for_status()

            # 2. build the graph from it, on their side, in the background
            cognify = await client.post(
                _url("/api/v1/cognify"),
                headers=_headers(),
                json={"datasets": [DATASET], "run_in_background": True},
            )
            cognify.raise_for_status()

        print("[cognee] remembered: %s" % text[:90])
        return True
    except Exception as exc:
        print("[cognee] remember skipped: %s: %s" % (type(exc).__name__, exc))
        return False


def remember_later(text: str) -> None:
    """Fire and forget — awarding a PO must not wait on the graph."""
    try:
        asyncio.get_running_loop().create_task(remember(text))
    except RuntimeError:
        pass


# =============================================================================
# READ
# =============================================================================

def _flatten(payload) -> str:
    """The answer arrives as [{search_result: <str | list | dict>}, ...]."""
    out = []
    for row in payload if isinstance(payload, list) else [payload]:
        value = row.get("search_result", row) if isinstance(row, dict) else row
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, list):
            out += [v if isinstance(v, str) else str(v.get("text", ""))
                    if isinstance(v, dict) else "" for v in value]
        elif isinstance(value, dict):
            out.append(str(value.get("text", "")))
    return " ".join(s.strip() for s in out if s and s.strip())


async def recall(question: str, timeout: float = 20.0) -> str:
    """Ask the memory a question. Returns '' when it has nothing to say."""
    if not enabled():
        return ""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                _url("/api/v1/search"),
                headers=_headers(),
                json={"query": question,
                      "search_type": "GRAPH_COMPLETION",
                      "datasets": [DATASET],
                      "top_k": 5},
            )
            response.raise_for_status()
            answer = _flatten(response.json())

        if answer:
            print("[cognee] recalled: %s" % answer[:120])
        return answer[:600]
    except Exception as exc:
        print("[cognee] recall skipped: %s: %s" % (type(exc).__name__, exc))
        return ""
