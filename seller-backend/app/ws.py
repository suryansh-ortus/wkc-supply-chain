"""
WebSocket fan-out. One process, in-memory. No Redis.

Channels:
    seller:<seller_id>      everything that seller should see
    buyer                   the buyer console
    negotiation:<id>        a single negotiation room (both sides)
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

from fastapi import WebSocket


class Hub:
    def __init__(self) -> None:
        self._channels: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def join(self, channel: str, ws: WebSocket) -> None:
        async with self._lock:
            self._channels[channel].add(ws)

    async def leave_all(self, ws: WebSocket) -> None:
        async with self._lock:
            for members in self._channels.values():
                members.discard(ws)

    async def send(self, channel: str, event: str, data: dict) -> None:
        """Push to everyone in a channel. Dead sockets are dropped."""
        async with self._lock:
            members = list(self._channels.get(channel, ()))

        if not members:
            return

        payload = {"event": event, "channel": channel, "data": data}
        dead = []
        for ws in members:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    for members_set in self._channels.values():
                        members_set.discard(ws)

    async def send_many(self, channels: list[str], event: str, data: dict) -> None:
        for channel in channels:
            await self.send(channel, event, data)


hub = Hub()
