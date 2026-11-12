"""Sendler Dash live events: one tailable cursor on the indexer's capped `dash_events` per process,
fanned out to the subscribed WebSocket clients (/v2/dash/ws).

The cursor starts with the first client and keeps running; a client's queue is bounded — a slow
client loses events instead of holding the others back.
"""
from __future__ import annotations

import asyncio
import json
import logging

from pymongo import CursorType

from config import dash as cfg
from core.database import Database
from services.dash_kit import jsonable

logger = logging.getLogger(__name__)


class Client:
    __slots__ = ("ip", "subs", "queue")

    def __init__(self, ip: str) -> None:
        self.ip = ip
        self.subs: set[str] = set()
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=cfg.WS_QUEUE)


class Hub:
    def __init__(self) -> None:
        self.clients: set[Client] = set()
        self._tail: asyncio.Task | None = None

    def per_ip(self, ip: str) -> int:
        return sum(1 for c in self.clients if c.ip == ip)

    def join(self, client: Client) -> None:
        self.clients.add(client)
        if self._tail is None or self._tail.done():
            self._tail = asyncio.create_task(self._tail_events())

    def leave(self, client: Client) -> None:
        self.clients.discard(client)

    def subscribe(self, client: Client, op: str, chans: list) -> dict:
        """Client message {"op": "sub"|"unsub", "ch": [...]} → reply. A reply always has `ok` or `error` and never
        `ch`: only events carry `ch`, so a client tells them apart by that field."""
        chans = [c for c in chans if isinstance(c, str)]
        bad = [c for c in chans if c.split(":", 1)[0] not in cfg.CHANNELS]
        if bad:
            return {"error": "unknown_channel", "bad": bad}
        if op == "sub":
            if len(client.subs | set(chans)) > cfg.WS_MAX_SUBS:
                return {"error": "too_many_subs", "max": cfg.WS_MAX_SUBS}
            client.subs.update(chans)
        elif op == "unsub":
            client.subs.difference_update(chans)
        elif op != "ping":
            return {"error": "bad_op"}
        return {"ok": True, "subs": sorted(client.subs)}

    def dispatch(self, doc: dict) -> None:
        """Event → queues of subscribed clients (`trade` or `trade:<token>`); full queue drops it."""
        ch = doc.get("ch")
        data = doc.get("d") or {}
        token_ch = f"{ch}:{data.get('token')}" if data.get("token") else None
        text = None
        for c in self.clients:
            if ch in c.subs or (token_ch and token_ch in c.subs):
                if text is None:
                    text = json.dumps({"ch": ch, "ts": jsonable(doc.get("ts")), "d": jsonable(data)},
                                      ensure_ascii=False, separators=(",", ":"))
                try:
                    c.queue.put_nowait(text)
                except asyncio.QueueFull:
                    pass

    async def _tail_events(self) -> None:
        coll = Database.get_db("near_indexer")[cfg.EVENTS_COLLECTION]
        last = None
        while True:
            try:
                if last is None:
                    top = await coll.find_one({}, sort=[("$natural", -1)])
                    last = top["_id"] if top else None
                flt = {"_id": {"$gt": last}} if last is not None else {}
                cursor = coll.find(flt, cursor_type=CursorType.TAILABLE_AWAIT)
                while cursor.alive:
                    async for doc in cursor:
                        last = doc["_id"]
                        if self.clients:
                            self.dispatch(doc)
                    await asyncio.sleep(0.2)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # collection missing / cursor died: reopen
                logger.warning("dash events tail: %s", str(e)[:200])
                await asyncio.sleep(2)

    async def stop(self) -> None:
        if self._tail:
            self._tail.cancel()
            try:
                await self._tail
            except (asyncio.CancelledError, Exception):
                pass
            self._tail = None


HUB = Hub()
