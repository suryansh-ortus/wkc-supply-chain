"""
Push a websocket event, wherever the recipient is connected.

The seller service holds seller sockets and negotiation rooms. The buyer
console is on the buyer service, so events for it are forwarded over HTTP.
"""

from __future__ import annotations

import httpx

from app import config
from app.ws import hub


async def send(channel: str, event: str, data: dict) -> None:
    if channel.startswith("seller:") or channel.startswith("negotiation:"):
        await hub.send(channel, event, data)
        return
    await _forward(channel, event, data)


async def send_many(channels: list[str], event: str, data: dict) -> None:
    for c in channels:
        await send(c, event, data)


async def _forward(channel: str, event: str, data: dict) -> None:
    if not config.BUYER_SERVICE_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            await http.post(
                f"{config.BUYER_SERVICE_URL}/internal/push",
                headers={"X-Internal-Secret": config.INTERNAL_SECRET},
                json={"channel": channel, "event": event, "data": data},
            )
    except Exception as exc:
        print(f"  notify -> buyer service failed ({type(exc).__name__}: {exc})")


# --- calling the buyer's agent ----------------------------------------------

async def call_agent(path: str, payload: dict) -> dict:
    """
    The seller service never runs the agent. It tells the buyer service that
    something happened and the agent does its work there.
    """
    if not config.BUYER_SERVICE_URL:
        return {"error": "BUYER_SERVICE_URL not configured"}
    try:
        async with httpx.AsyncClient(timeout=120.0) as http:
            res = await http.post(
                f"{config.BUYER_SERVICE_URL}/internal/{path}",
                headers={"X-Internal-Secret": config.INTERNAL_SECRET},
                json=payload,
            )
            res.raise_for_status()
            return res.json()
    except Exception as exc:
        print(f"  agent call {path} failed ({type(exc).__name__}: {exc})")
        return {"error": f"{type(exc).__name__}: {exc}"}
