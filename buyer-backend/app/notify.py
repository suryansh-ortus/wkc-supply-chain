"""
Push a websocket event, wherever the recipient is connected.

The buyer service holds buyer sockets. Seller sockets live on the seller
service. Anything addressed to a seller channel is forwarded there over HTTP.
"""

from __future__ import annotations

import httpx

from app import config
from app.ws import hub

MINE = ("buyer",)


async def send(channel: str, event: str, data: dict) -> None:
    if channel in MINE:
        await hub.send(channel, event, data)
        return
    await _forward(channel, event, data)


async def send_many(channels: list[str], event: str, data: dict) -> None:
    for c in channels:
        await send(c, event, data)


async def _forward(channel: str, event: str, data: dict) -> None:
    """Hand the event to the seller service to deliver."""
    if not config.SELLER_SERVICE_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            await http.post(
                f"{config.SELLER_SERVICE_URL}/internal/push",
                headers={"X-Internal-Secret": config.INTERNAL_SECRET},
                json={"channel": channel, "event": event, "data": data},
            )
    except Exception as exc:
        print(f"  notify -> seller service failed ({type(exc).__name__}: {exc})")
