"""What the creator did in each Nearly / JustHoot launch -> `sniper_launch_stats` (the creator dossier of the API).

How many of his own tokens the creator received (dev buy at the start, fees), how many he sold and when, whether he
sent them to other wallets. The source is the block stream, no RPC:
- nep141 ft_transfer in the token's receipts: the creator received (new_owner_id) or gave away (old_owner_id); to the
  DCL - a sale, to another wallet - a hand-out; the JustHoot v2 dev buy arrives as ft_mint to the creator;
- dcl.ref swap with swapper = creator and token_in = the token: the sale's amount in NEAR (pools against wNEAR);
- start of trading (t0) - the first transfer of the token from the DCL to a buyer.

A launch is watched from its `started` event for WATCH_S after trading starts; DUMP_WINDOW_S into trading the launch
is marked `dumped` if the creator sold >= DUMP_SHARE of what he received (on mainnet, 9-17 Oct 2026: 15% of
launches). Writes go to Mongo in the background; after a restart unfinished launches are picked up from Mongo.
The collection name is historical: the same data feeds the Sendler trade bot.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from dataclasses import dataclass, field

from services.chain.block import DCL, WNEAR, events, header, items, outcomes

from .feed import BlockParser

logger = logging.getLogger("dashboard")

WATCH_S = 3600.0  # watch a launch for an hour from the start of trading
DUMP_WINDOW_S = 300.0  # "dumped" - within the first 5 minutes of trading
DUMP_SHARE = 0.5  # sold >= half of what he received
LAUNCHPADS = ("nearly", "justhoot")
TTL_S = 90 * 86400


def dumped(dev_in: int, sold_window: int) -> bool:
    return dev_in > 0 and sold_window >= dev_in * DUMP_SHARE


@dataclass
class Stat:
    token: str
    creator: str
    launchpad: str
    pool: str | None = None
    started_at: float = 0.0
    t0: float | None = None  # start of trading (first transfer out of the DCL)
    dev_in: int = 0  # tokens received by the creator
    dev_sold: int = 0  # sold into the DCL
    dev_sold_near: float = 0.0  # NEAR received for the sales (pools against wNEAR only)
    dev_out: int = 0  # sent to other wallets
    sold_window: int = 0  # sold within the first DUMP_WINDOW_S of trading
    first_sell_at: float | None = None
    dumped: bool | None = None  # None - the window is still open
    sells: int = 0
    seen: set = field(default_factory=set)  # transfers already counted (the stream may repeat a block)

    def apply(self, old: str | None, new: str | None, amount: int, ts: float) -> bool:
        """A transfer of the launch token. Returns whether anything changed."""
        changed = False
        if old == DCL and self.t0 is None and new != self.token:
            self.t0 = ts
            changed = True
        if new == self.creator:
            self.dev_in += amount
            return True
        if old == self.creator:
            if new == DCL:
                self.dev_sold += amount
                self.sells += 1
                self.first_sell_at = self.first_sell_at or ts
                if self.t0 is None or ts - self.t0 <= DUMP_WINDOW_S:
                    self.sold_window += amount
            else:
                self.dev_out += amount
            return True
        return changed

    def doc(self) -> dict:
        return {"_id": self.token, "creator": self.creator, "launchpad": self.launchpad, "pool": self.pool,
                "started_at": self.started_at, "t0": self.t0, "dev_in": str(self.dev_in),
                "dev_sold": str(self.dev_sold), "dev_sold_near": round(self.dev_sold_near, 6),
                "dev_out": str(self.dev_out), "sold_window": str(self.sold_window),
                "first_sell_at": self.first_sell_at,
                "first_sell_s": (self.first_sell_at - self.t0) if self.first_sell_at and self.t0 else None,
                "dumped": self.dumped, "sells": self.sells, "updated_at": dt.datetime.now(dt.timezone.utc)}

    @classmethod
    def from_doc(cls, d: dict) -> Stat:
        return cls(d["_id"], d["creator"], d.get("launchpad") or "", d.get("pool"), float(d.get("started_at") or 0),
                   d.get("t0"), int(d.get("dev_in") or 0), int(d.get("dev_sold") or 0),
                   float(d.get("dev_sold_near") or 0), int(d.get("dev_out") or 0), int(d.get("sold_window") or 0),
                   d.get("first_sell_at"), d.get("dumped"), int(d.get("sells") or 0))


class LaunchStats:
    """Block stream subscriber with its own launch feed parser (the parser keeps state between blocks)."""

    def __init__(self, db) -> None:
        self.col = db["sniper_launch_stats"] if db is not None else None
        self.parser = BlockParser()
        self.active: dict[str, Stat] = {}
        self._dirty: dict[str, dict] = {}  # token -> document waiting to be written
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        """Indexes, and the launches whose watch has not ended (after a restart)."""
        await self.col.create_index("creator")
        await self.col.create_index("updated_at", expireAfterSeconds=TTL_S)
        since = time.time() - WATCH_S
        async for d in self.col.find({"started_at": {"$gte": since}}):
            self.active[d["_id"]] = Stat.from_doc(d)

    def track(self, ev) -> None:
        """A launch from the feed (started, or ready if started was missed): start watching."""
        if ev.launchpad not in LAUNCHPADS or not ev.token or not ev.creator or ev.token in self.active:
            return
        pool = ev.pool if ev.pool and "|" in ev.pool else None
        st = self.active[ev.token] = Stat(ev.token, ev.creator, ev.launchpad, pool, ev.block_ts or time.time())
        self._dirty[st.token] = st.doc()

    async def handle(self, block: dict) -> None:
        try:
            for ev in self.parser.parse(block):
                self.track(ev)
        except Exception:  # noqa: BLE001 - the feed must not break the subscriber
            logger.exception("launch stats: launch feed failed")
        if self.active:
            self.apply(block)
        if self._dirty and self.col is not None:
            docs, self._dirty = self._dirty, {}
            task = asyncio.ensure_future(self._write(docs))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    def apply(self, block: dict) -> None:
        """Transfers and creator sales of the watched launches in this block."""
        _, ts = header(block)
        ts = ts or time.time()
        changed: set[str] = set()
        for o in outcomes(block):
            try:
                if o.executor in self.active:
                    if self._transfers(self.active[o.executor], o.logs, o.receipt_id, ts):
                        changed.add(o.executor)
                elif o.executor == DCL:
                    changed |= self._swaps(o.logs)
            except (KeyError, TypeError, ValueError) as e:
                logger.debug("launch stats: receipt not parsed: %s", e)
        for token in list(self.active):
            st = self.active[token]
            if st.dumped is None and st.t0 and ts - st.t0 >= DUMP_WINDOW_S:
                st.dumped = dumped(st.dev_in, st.sold_window)
                changed.add(token)
            if (st.t0 or st.started_at) and ts - (st.t0 or st.started_at) > WATCH_S:
                self._dirty[token] = st.doc()
                self.active.pop(token, None)
                changed.discard(token)
        for token in changed:
            self._dirty[token] = self.active[token].doc()

    def _transfers(self, st: Stat, logs: list[str], rid, ts: float) -> bool:
        changed = False
        for n, ev in enumerate(events(logs)):  # one receipt may carry several events (mint to the pool and to the dev)
            name = ev.get("event")
            if ev.get("standard") != "nep141" or name not in ("ft_transfer", "ft_mint"):
                continue
            for i, d in enumerate(items(ev.get("data"))):
                key = (rid, n, i)
                if rid and key in st.seen:
                    continue
                st.seen.add(key)
                old, new = ((None, d.get("owner_id")) if name == "ft_mint"
                            else (d.get("old_owner_id"), d.get("new_owner_id")))
                changed |= st.apply(old, new, int(d.get("amount") or 0), ts)
        return changed

    def _swaps(self, logs: list[str]) -> set[str]:
        """Creator's sales in the DCL: NEAR received (pools against wNEAR)."""
        out: set[str] = set()
        for ev in events(logs):
            if ev.get("standard") != "dcl.ref" or ev.get("event") != "swap":
                continue
            for d in items(ev.get("data")):
                st = self.active.get(d.get("token_in") or "")
                if st is None or d.get("swapper") != st.creator or d.get("token_out") != WNEAR:
                    continue
                st.dev_sold_near += int(d.get("amount_out") or 0) / 1e24
                out.add(st.token)
        return out

    async def _write(self, docs: dict[str, dict]) -> None:
        for token, doc in docs.items():
            try:
                await self.col.update_one({"_id": token}, {"$set": doc}, upsert=True)
            except Exception as e:  # noqa: BLE001 - statistics must not break the stream
                logger.warning("launch stats: %s not written: %s", token, str(e)[:200])

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
