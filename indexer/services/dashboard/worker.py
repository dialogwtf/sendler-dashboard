"""Sendler Dash worker: a subscriber of the block stream -> dash_* collections.

Writes: dash_trades and dash_candles (trades and candles from swaps), dash_launches (launches and their price),
dash_moves (transfers and swaps from MOVE_MIN_USD), dash_network (network metrics), dash_new (new contracts, tokens,
NFTs, pools), dash_labels (new contracts - auto), dash_events (feed for the API's WebSocket), dash_state (cursor, lease).

- the subscriber (on_block) only puts the block into a queue and returns at once: the stream calls subscribers in
  turn and nobody may wait for the dashboard. Queue full - the block is dropped (dash_dropped_blocks_total), refetched below;
- gaps are seen by prev_height (NEAR has heights without a block - a "hole" in the numbers means nothing by itself):
  missing heights are refetched by number from neardata (one retry), but no more than DASH_GAP_MAX;
- writes go in one batch every FLUSH_S; trades, moves and new items - upsert by key (a repeat is safe), candles and
  network - increments; the height - in dash_state.cursor;
- one writer per cluster - a lease in dash_state.lease;
- event loop lag of the process - dash_loop_lag_ms: the dashboard may share a process with other workers; if it
  grows, the worker moves to its own process with the same code.

The worker makes no RPC requests; it calls neardata only to refetch gaps.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import socket
import time

from pymongo import ASCENDING, DESCENDING, UpdateOne
from pymongo.errors import BulkWriteError, DuplicateKeyError

from config import Settings
from services.chain.block import DCL, INTEAR_ENGINE, REF, WNEAR, header
from services.chain.swaps import block_swaps

from .blocks import (
    INTEAR_XYK,
    NFT_STANDARD,
    discoveries,
    intear_pools,
    net_stats,
    token_standards,
    transfers,
)
from .events import EVENTS, ensure_events, event, seed_labels, sync_hub_labels
from .holders import STATS_S, HolderBook, stats_op
from .launches import LaunchTracker
from .network import BUCKETS, NetworkAgg
from .prices import STABLE_DECIMALS, UsdBook
from .trades import Candles, MainPools, Trade, arbitrage_receipts, trade_of

logger = logging.getLogger("dashboard")

FLUSH_S = 2.0
LEASE_TTL_S = 30.0
LEASE_RENEW_S = 10.0
LEASE_RETRY_S = 15.0
LAG_EVERY_S = 1.0
FETCH_RETRY_S = 1.0
LEASE_ID = "lease"
CURSOR_ID = "cursor"
INTEAR_ID = "intear"  # dash_state: the highest known Intear pool number
HUB_SYNC_S = 3600.0
LINKED_MAX = 200_000  # tokens with a written bot link code - beyond that the memory is reset (the upsert is safe)
MOVE_MIN_USD = 1_000.0
MOVES_TTL_DAYS = 30
NEW_TTL_DAYS = 90
FRESH_KEEP_S = 86400.0
# transfers involving venues are swap legs and (un)wrap: a big swap gets into moves as a swap anyway
VENUE_ACCOUNTS = frozenset({DCL, REF, INTEAR_ENGINE, WNEAR, "aggregatedex.near"})


def _metric(name: str, value: float, gauge: bool = True) -> None:
    try:
        from health.server import inc, set_gauge

        (set_gauge if gauge else inc)(name, value)
    except Exception:  # noqa: BLE001
        pass


def heights(block: dict) -> tuple[int, int | None]:
    """(height, prev_height) of a neardata block; no prev_height - None."""
    h = (block.get("block") or {}).get("header") or {}
    prev = h.get("prev_height")
    return int(h.get("height") or 0), int(prev) if prev is not None else None


def _at(ts: float) -> dt.datetime:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc)


class DashWorker:
    def __init__(self, db=None, stream=None, fetch=None) -> None:
        self.db = db
        self.stream = stream
        self._fetch = fetch  # async (height) -> block | None; by default - neardata through the stream
        self.holder = f"{socket.gethostname()}:{os.getpid()}"
        self.height = 0
        self.queue: asyncio.Queue | None = None
        self.candles = Candles()
        self.pools = MainPools()
        self.network = NetworkAgg()
        self.launches = LaunchTracker()
        self.holders = HolderBook()
        self._holder_ops: list[UpdateOne] = []  # not written because of a batch error - retried with the next flush
        self._next_holder_stats = 0.0
        self.prices = UsdBook(db)
        self.trades: list[Trade] = []
        self.moves: list[dict] = []
        self.new: list[UpdateOne] = []
        self.labels: list[UpdateOne] = []
        self.events: list[dict] = []
        self._fresh: dict[str, float] = {}  # new contracts of the last day -> when (type refinement)
        self._typed: set[str] = set()
        self._net_window = [0, 0, 0]  # blocks, transactions, receipts since the last flush - the network event
        self.intear_max: int | None = None  # the highest Intear pool number: above it - a new pool
        self._intear_dirty = False
        self.links: set[str] = set()  # tokens whose bot link code is not written yet (until the flush)
        self._linked: set[str] = set()  # already written in this process
        self.dropped = 0
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True

    # ── start ───────────────────────────────────────────────────────────────

    async def run(self) -> None:
        if not Settings.DASH_ENABLED:
            logger.info("Sendler Dash: DASH_ENABLED is off - the worker is not started")
            return
        if self.db is None:
            from database.db import get_db

            self.db = get_db()
            self.prices.db = self.db
        if self.stream is None:
            from indexer import block_stream

            self.stream = block_stream.STREAM
        if self.stream is None:
            logger.warning("Sendler Dash: no block stream - the worker is not started")
            return
        await self.ensure_indexes()
        while not self._stopped:
            if not await self.acquire_lease():
                await asyncio.sleep(LEASE_RETRY_S)
                continue
            try:
                await self.serve()
            finally:
                if self._stopped:
                    await self.release_lease()

    async def ensure_indexes(self) -> None:
        db = self.db
        try:
            await db.dash_trades.create_index([("token", ASCENDING), ("ts", DESCENDING)])
            await db.dash_trades.create_index([("wallet", ASCENDING), ("ts", DESCENDING)])
            await db.dash_trades.create_index([("ts", DESCENDING)])
            await db.dash_trades.create_index("expires_at", expireAfterSeconds=0)
            await db.dash_candles.create_index([("token", ASCENDING), ("i", ASCENDING), ("t", DESCENDING)])
            await db.dash_candles.create_index([("i", ASCENDING), ("t", DESCENDING)])
            await db.dash_candles.create_index("expires_at", expireAfterSeconds=0)
            await db.dash_launches.create_index([("t_first", DESCENDING)])
            await db.dash_launches.create_index([("creator", ASCENDING), ("t_first", DESCENDING)])
            await db.dash_launches.create_index([("last_trade", DESCENDING)])
            await db.dash_moves.create_index([("ts", DESCENDING)])
            await db.dash_moves.create_index([("token", ASCENDING), ("ts", DESCENDING)])
            await db.dash_moves.create_index([("usd", DESCENDING), ("ts", DESCENDING)])
            await db.dash_moves.create_index("expires_at", expireAfterSeconds=0)
            await db.dash_network.create_index([("i", ASCENDING), ("t", DESCENDING)])
            await db.dash_network.create_index("expires_at", expireAfterSeconds=0)
            await db.dash_new.create_index([("kind", ASCENDING), ("first_seen", DESCENDING)])
            await db.dash_new.create_index([("first_seen", DESCENDING)])
            await db.dash_new.create_index([("token_kind", ASCENDING), ("first_seen", DESCENDING)])
            # the API's creator dossier (/v2/dash) looks up blocks by account across all users
            await db.sniper_devs_blocked.create_index("account")
            # holders in the API: who is linked to the launch's creator (links.root = creator)
            await db.sniper_dev_links.create_index("links.root")
            await db.dash_new.create_index("expires_at", expireAfterSeconds=0)
            await db.dash_labels.create_index("kind")
            await db.dash_holders.create_index([("token", ASCENDING), ("bal", DESCENDING)])
            await db.dash_holders.create_index([("account", ASCENDING), ("token", ASCENDING)])
        except Exception as e:  # noqa: BLE001 - indexes must not prevent the start
            logger.warning("Sendler Dash: indexes not created: %s", e)
        await ensure_events(db)
        await seed_labels(db)

    # ── lease ───────────────────────────────────────────────────────────────

    async def acquire_lease(self) -> bool:
        t = time.time()
        try:
            await self.db.dash_state.update_one(
                {"_id": LEASE_ID, "$or": [{"expires": {"$lt": t}}, {"holder": self.holder}]},
                {"$set": {"holder": self.holder, "expires": t + LEASE_TTL_S}}, upsert=True)
            return True
        except DuplicateKeyError:
            return False

    async def release_lease(self) -> None:
        try:
            await self.db.dash_state.delete_one({"_id": LEASE_ID, "holder": self.holder})
        except Exception:  # noqa: BLE001
            pass

    # ── main loop ───────────────────────────────────────────────────────────

    async def warm_up(self) -> None:
        """After a restart: cursor, rates, launches (their trades update the price), fresh contracts."""
        cur = await self.db.dash_state.find_one({"_id": CURSOR_ID}) or {}
        self.height = int(cur.get("height") or 0)
        await self.prices.refresh(force=True)
        try:
            # all launches, not only recent ones: otherwise after a restart old launches that still trade would freeze their
            # px / last_trade / volume (before 11 Nov only 3 days were loaded - IMEME dropped out of "Movers" with the price of
            # 9 Nov), and the worker would not track their holders. There are thousands of launches, it is one query by _id.
            docs = [d async for d in self.db.dash_launches.find({}, {"px0": 1, "backfill": 1, "pool": 1, "creator": 1})]
            self.launches.load(docs)
            self.launches.load_quotes([d for d in docs if d.get("pool")])
            for d in docs:
                self.holders.track(d["_id"])
            # pair asset prices - from our own hourly candles (the main pool's close): otherwise after a restart trades in
            # pairs with PRL/QTC/TEAR would be skipped until the asset's first swap against NEAR
            for q in sorted(self.launches.quotes):
                c = await self.db.dash_candles.find_one({"token": q, "i": "1h"}, {"c": 1, "t": 1}, sort=[("t", -1)])
                if c and c.get("c") and isinstance(c.get("t"), dt.datetime):
                    t = c["t"] if c["t"].tzinfo else c["t"].replace(tzinfo=dt.timezone.utc)  # the database returns UTC without a timezone
                    self.prices.note_trade(q, float(c["c"]), t.timestamp() + 3600)
            day = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=FRESH_KEEP_S)
            async for d in self.db.dash_new.find({"kind": "contract", "first_seen": {"$gte": day}}, {"first_seen": 1}):
                self._fresh[d["_id"].split(":", 1)[1]] = d["first_seen"].timestamp()
            self.intear_max = await self._intear_known()
        except Exception as e:  # noqa: BLE001
            logger.warning("Sendler Dash: warm-up failed: %s", e)

    async def _intear_known(self) -> int | None:
        """The highest known Intear pool number: our own (dash_state) or from the trade bot's pool index. Neither - None:
        the first pools seen become the baseline, not new items."""
        doc = await self.db.dash_state.find_one({"_id": INTEAR_ID}) or {}
        if doc.get("max") is not None:
            return int(doc["max"])
        idx = await self.db.sniper_stream_state.find_one({"_id": "index:intear"}, {"pools": 1}) or {}
        ids = [int(p[0]) for p in idx.get("pools") or [] if p]
        return max(ids) if ids else None

    async def serve(self) -> None:
        await self.warm_up()
        self.queue = asyncio.Queue(maxsize=Settings.DASH_QUEUE)
        unsubscribe = self.stream.subscribe(self.on_block)
        lag = asyncio.create_task(self._watch_lag())
        logger.info("Sendler Dash: worker started from height %s", self.height or "the head of the stream")
        loop = asyncio.get_running_loop()
        next_flush = loop.time() + FLUSH_S
        next_lease = loop.time() + LEASE_RENEW_S
        next_hubs = loop.time()
        try:
            while not self._stopped:
                if loop.time() >= next_hubs:
                    await sync_hub_labels(self.db)
                    await self._link_smart()
                    next_hubs = loop.time() + HUB_SYNC_S
                try:
                    block = await asyncio.wait_for(self.queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    block = None
                if block is not None:
                    try:
                        await self.process(block)
                    except Exception:  # noqa: BLE001 - one block does not stop the worker
                        logger.exception("Sendler Dash: block not parsed")
                now = loop.time()
                if now >= next_flush:
                    await self.flush()
                    next_flush = now + FLUSH_S
                if now >= next_lease:
                    if not await self.acquire_lease():
                        logger.warning("Sendler Dash: lease lost - the worker stops")
                        return
                    next_lease = now + LEASE_RENEW_S
        finally:
            unsubscribe()
            lag.cancel()
            await self.flush()

    async def on_block(self, block: dict) -> None:
        """Stream subscriber: into the queue only, no waiting."""
        if self.queue is None:
            return
        try:
            self.queue.put_nowait(block)
        except asyncio.QueueFull:
            self.dropped += 1
            _metric("dash_dropped_blocks_total", 1, gauge=False)
        _metric("dash_queue", self.queue.qsize())

    async def process(self, block: dict) -> None:
        height, prev = heights(block)
        if not height or height <= self.height:
            return  # a repeat or an old block
        if self.height and prev is not None and prev > self.height:
            await self.fill(self.height + 1, prev)
        await self.consume(block)
        self.height = height
        _metric("dash_height", height)

    async def fill(self, lo: int, hi: int) -> None:
        """Refetch heights lo..hi (inclusive) by number; longer than DASH_GAP_MAX - skip."""
        n = hi - lo + 1
        if n > Settings.DASH_GAP_MAX:
            logger.warning("Sendler Dash: gap of %s heights (%s–%s) not refetched - longer than %s", n, lo, hi,
                           Settings.DASH_GAP_MAX)
            _metric("dash_gap_skipped_blocks_total", n, gauge=False)
            return
        for h in range(lo, hi + 1):
            if self._stopped:
                return
            try:
                block = await self.fetch(h)
            except Exception:  # noqa: BLE001 - a one-off 429 / timeout: one retry
                await asyncio.sleep(FETCH_RETRY_S)
                try:
                    block = await self.fetch(h)
                except Exception as e:  # noqa: BLE001
                    logger.warning("Sendler Dash: height %s not refetched: %s", h, str(e)[:200])
                    _metric("dash_gap_skipped_blocks_total", 1, gauge=False)
                    continue
            if block:
                await self.consume(block)
            _metric("dash_gap_filled_blocks_total", 1, gauge=False)

    async def fetch(self, height: int) -> dict | None:
        if self._fetch is not None:
            return await self._fetch(height)
        return await self.stream._get(f"/block/{height}", missing_ok=True)

    async def consume(self, block: dict) -> None:
        _, ts = header(block)
        await self._ensure_hll(ts)
        self._trades(block)
        self._transfers(block, ts)
        stats = net_stats(block)
        self.network.add(ts, stats)
        w = self._net_window
        w[0], w[1], w[2] = w[0] + 1, w[1] + stats.txs, w[2] + stats.receipts
        self._discover(block, ts)
        self.launches.parse(block)
        for token in self.launches.fresh:  # a new launch's mint comes after started - already with its own token
            self.holders.track(token)
        self.launches.fresh = []
        self.holders.add(block)

    def _via(self, a: str, ts: float) -> float | None:
        """A pair asset's price in NEAR at the block's time (trades in pairs not against NEAR): stables and launch pair assets."""
        if a in STABLE_DECIMALS or a in self.launches.quotes:
            return self.prices.near_per_raw(a, ts)
        return None

    def _trades(self, block: dict) -> None:
        min_near = Settings.DASH_TRADE_MIN_NEAR
        near_usd = self.prices.near_usd
        swaps = list(block_swaps(block))
        arb = arbitrage_receipts(swaps)
        # swap by swap: a route NEAR -> PRL -> token in one receipt prices the token at the PRL price just updated
        for bs in swaps:
            t = trade_of(bs, bs.receipt_id in arb, lambda x, ts=bs.ts: self._via(x, ts))
            if t is None:
                continue
            main = self.pools.note(t)  # only the token's main pool sets the price
            self.candles.add(t, price=main)
            if main:
                self.prices.note_trade(t.token, t.px, t.ts)
            self.launches.note_trade(t.token, t.px, t.ts, t.near, price=main)
            self.note_link(t.token)
            if t.arb or t.near < min_near:
                continue
            self.trades.append(t)
            usd = t.near * near_usd if near_usd else None
            if usd is not None and usd >= MOVE_MIN_USD:
                self.moves.append({"_id": t.key, "kind": "swap", "ts": _at(t.ts), "token": t.token,
                                   "side": t.side, "amount": str(t.tok), "near": t.near, "usd": usd,
                                   "from": t.wallet, "to": t.pool, "signer": t.signer, "tx": t.tx, "venue": t.venue})

    def note_link(self, token: str) -> None:
        """A token the site will link to with "Buy in the bot": the bot finds it by code only if the code is written to
        sniper_token_links (the code is derived from the address and cannot be reversed)."""
        if token not in self._linked:
            self.links.add(token)

    async def _link_smart(self) -> None:
        """Hourly, together with hub labels: bot link codes for smart wallets (top 200, "Watch" from the site) and for all
        ft_metadata tokens - the site's search finds them there, and "Buy in the bot" on the page of a token without
        trades against NEAR (USDT, wNEAR, old tokens) would otherwise open only the menu. Repeats are cut off by memory
        (`_linked`): after the first pass only new ones are written."""
        for coll, what in (("sniper_smart_wallets", "smart wallets"), ("ft_metadata", "tokens")):
            try:
                async for d in self.db[coll].find({}, {"_id": 1}):
                    if isinstance(d.get("_id"), str):
                        self.note_link(d["_id"])
            except Exception as e:  # noqa: BLE001
                logger.warning("Sendler Dash: %s for links not read: %s", what, e)

    async def _write_links(self) -> None:
        if not self.links:
            return
        from .botlinks import token_code

        now = dt.datetime.now(dt.timezone.utc)
        tokens, self.links = self.links, set()
        ops = [UpdateOne({"_id": token_code(t)}, {"$setOnInsert": {"token": t, "at": now}}, upsert=True)
               for t in tokens]
        try:
            await self.db.sniper_token_links.bulk_write(ops, ordered=False)
        except Exception as e:  # noqa: BLE001 - not written - we will try with the next trade
            logger.warning("Sendler Dash: bot link codes not written: %s", str(e)[:200])
            return
        if len(self._linked) > LINKED_MAX:
            self._linked.clear()
        self._linked |= tokens

    def _transfers(self, block: dict, ts: float) -> None:
        for tr in transfers(block):
            if tr.sender == tr.receiver or tr.sender in VENUE_ACCOUNTS or tr.receiver in VENUE_ACCOUNTS:
                continue
            usd = self.prices.usd(tr.token, tr.amount, ts)
            if usd is None or usd < MOVE_MIN_USD:
                continue
            self.moves.append({"_id": tr.key, "kind": "transfer", "ts": _at(ts), "token": tr.token,
                               "amount": str(tr.amount), "usd": usd, "from": tr.sender, "to": tr.receiver,
                               "signer": tr.signer, "tx": tr.tx_hash})

    def _discover(self, block: dict, ts: float) -> None:
        at = _at(ts)
        for d in discoveries(block):
            doc = {"kind": d.kind, "id": d.id, "by": d.by, "tx": d.tx_hash, "first_seen": at,
                   "expires_at": at + dt.timedelta(days=NEW_TTL_DAYS), **d.extra}
            self.new.append(UpdateOne({"_id": f"{d.kind}:{d.id}"}, {"$setOnInsert": doc}, upsert=True))
            self.events.append(event("new", {"kind": d.kind, "id": d.id, "by": d.by}, at))
            for tok in d.extra.get("tokens") or ():
                self.note_link(tok)  # "New" leads from a pool to its token's page, and "Buy in the bot" is there
            if d.kind == "contract":
                self._fresh[d.id] = ts
                self.labels.append(UpdateOne({"_id": d.id}, {"$setOnInsert": {
                    "kind": "contract", "source": "auto", "updated_at": at}}, upsert=True))
        for p in intear_pools(block):
            if self.intear_max is not None and p.pool_id <= self.intear_max:
                continue
            known, self.intear_max, self._intear_dirty = self.intear_max, p.pool_id, True
            if known is None:
                continue  # the baseline, not a new item
            pid = f"{INTEAR_XYK}#{p.pool_id}"
            self.new.append(UpdateOne({"_id": f"pool:{pid}"}, {"$setOnInsert": {
                "kind": "pool", "id": pid, "by": p.by, "tx": p.tx_hash, "first_seen": at,
                "expires_at": at + dt.timedelta(days=NEW_TTL_DAYS), "venue": "Intear DEX", "tokens": list(p.tokens),
                "pool_kind": p.kind}}, upsert=True))
            self.events.append(event("new", {"kind": "pool", "id": pid, "by": p.by}, at))
            for tok in p.tokens:
                if tok != "near":
                    self.note_link(tok)
        for account, std in token_standards(block):
            if account in self._fresh and account not in self._typed:
                self._typed.add(account)
                kind = "nft" if std == NFT_STANDARD else "ft"
                self.new.append(UpdateOne({"_id": f"contract:{account}"}, {"$set": {"token_kind": kind}}))
                self.events.append(event("new", {"kind": kind, "id": account}, at))
                if kind == "ft":
                    self.note_link(account)  # "Buy in the bot" for a new token - before its first trade
        if len(self._fresh) > 20_000:
            cut = ts - FRESH_KEEP_S
            self._fresh = {a: t for a, t in self._fresh.items() if t >= cut}
            self._typed &= set(self._fresh)

    async def _ensure_hll(self, ts: float) -> None:
        """Registers of unique signers of the current hour and day - from the database, so a restart does not reset the count."""
        for name in ("1h", "1d"):
            start = int(ts // BUCKETS[name] * BUCKETS[name])
            if self.network.known(name, start):
                continue
            regs = None
            try:
                doc = await self.db.dash_network.find_one({"_id": f"{name}:{start}"}, {"hll": 1})
                regs = (doc or {}).get("hll")
            except Exception:  # noqa: BLE001
                pass
            self.network.restore(name, start, regs)

    # ── writing ─────────────────────────────────────────────────────────────

    async def flush(self) -> None:
        await self.prices.refresh()
        usd = self.prices.near_usd
        now = dt.datetime.now(dt.timezone.utc)
        trades, self.trades = self.trades, []
        moves, self.moves = self.moves, []
        new, self.new = self.new, []
        labels, self.labels = self.labels, []
        launch_ops, launch_events = self.launches.drain()
        candle_ops = self.candles.drain(usd)
        net_ops = self.network.drain(time.time())
        events = self.events + [event("launch", e, e["at"]) for e in launch_events]
        self.events = []
        events += [event("trade", {"token": t.token, "side": t.side, "near": t.near, "usd": t.near * usd if usd else None,
                                   "px": t.px, "wallet": t.wallet, "venue": t.venue, "key": t.key}, _at(t.ts))
                   for t in trades]
        events += [event("move", {"id": m["_id"], **{k: v for k, v in m.items() if k != "_id"}}, m["ts"])
                   for m in moves]
        for e in launch_events:
            self.note_link(e["token"])
            if e.get("creator"):
                self.note_link(e["creator"])  # "Watch" the creator (start=ww_<code>)
        b, txs, rcpts = self._net_window
        if b:
            events.append(event("network", {"height": self.height, "blocks": b, "txs": txs, "receipts": rcpts}, now))
        self._net_window = [0, 0, 0]
        db = self.db
        try:
            if trades:
                ttl = Settings.DASH_TRADES_TTL_DAYS
                await db.dash_trades.bulk_write(
                    [UpdateOne({"_id": t.key}, {"$setOnInsert": t.doc(ttl, usd)}, upsert=True) for t in trades],
                    ordered=False)
                _metric("dash_trades_total", len(trades), gauge=False)
            if candle_ops:
                await db.dash_candles.bulk_write(candle_ops, ordered=False)
            if moves:
                await db.dash_moves.bulk_write(
                    [UpdateOne({"_id": m["_id"]}, {"$setOnInsert": {**m, "expires_at": m["ts"] + dt.timedelta(
                        days=MOVES_TTL_DAYS)}}, upsert=True) for m in moves], ordered=False)
            if launch_ops:
                try:  # a broken launch document must not bring down network, events and the window's cursor
                    await db.dash_launches.bulk_write(launch_ops, ordered=False)
                except Exception as e:  # noqa: BLE001
                    logger.warning("Sendler Dash: launches not written: %s", str(e)[:300])
                    _metric("dash_write_errors_total", 1, gauge=False)
            if net_ops:
                await db.dash_network.bulk_write(net_ops, ordered=False)
            if new:
                await db.dash_new.bulk_write(new, ordered=False)
            if labels:
                await db.dash_labels.bulk_write(labels, ordered=False)
            if events:
                await db[EVENTS].insert_many(events, ordered=False)
            if self.height:
                await db.dash_state.update_one({"_id": CURSOR_ID}, {"$set": {"height": self.height, "at": now}},
                                               upsert=True)
            if self._intear_dirty and self.intear_max is not None:
                await db.dash_state.update_one({"_id": INTEAR_ID}, {"$max": {"max": self.intear_max}}, upsert=True)
                self._intear_dirty = False
        except Exception as e:  # noqa: BLE001 - the write failed: the window's increments are lost, trades are not (repeat)
            logger.warning("Sendler Dash: write failed: %s", str(e)[:300])
            self.trades = trades + self.trades
            _metric("dash_write_errors_total", 1, gauge=False)
        await self._write_links()
        await self._write_holders(now)

    async def _write_holders(self, now: dt.datetime) -> None:
        """Balance increments go in a separate batch: losing them corrupts balances for good (until reconciled), so the
        operations not written because of a batch error are retried. Every STATS_S - the holder count of tokens with transfers."""
        ops = self._holder_ops + self.holders.drain(now)
        self._holder_ops = []
        if ops:
            try:
                await self.db.dash_holders.bulk_write(ops, ordered=False)
            except BulkWriteError as e:
                failed = {err.get("index") for err in (e.details or {}).get("writeErrors") or []}
                self._holder_ops = [op for i, op in enumerate(ops) if i in failed]
                logger.warning("Sendler Dash: holder balances partly written (%s errors) - will retry",
                               len(self._holder_ops))
                _metric("dash_write_errors_total", 1, gauge=False)
            except Exception as e:  # noqa: BLE001 - the batch's outcome is unknown: a retry could add twice
                logger.warning("Sendler Dash: holder balances not written (%s), reconciliation will fix them "
                               "scripts/backfill_dash_holders.py", str(e)[:200])
                _metric("dash_holders_lost_total", len(ops), gauge=False)
        if time.monotonic() < self._next_holder_stats:
            return
        self._next_holder_stats = time.monotonic() + STATS_S
        dirty, first = self.holders.take_dirty()
        if not dirty:
            return
        stats = []
        try:
            for token in sorted(dirty):
                n = await self.db.dash_holders.count_documents({"token": token, "bal": {"$gt": 0}})
                stats.append(stats_op(token, n, now, first.get(token)))
            await self.db.dash_holder_stats.bulk_write(stats, ordered=False)
        except Exception as e:  # noqa: BLE001 - the holder count is recomputed with the next transfer
            logger.warning("Sendler Dash: holder stats not written: %s", str(e)[:200])

    async def _watch_lag(self) -> None:
        """Event loop lag of the process: how much later than promised sleep wakes up."""
        loop = asyncio.get_running_loop()
        while True:
            t0 = loop.time()
            await asyncio.sleep(LAG_EVERY_S)
            _metric("dash_loop_lag_ms", max(0.0, (loop.time() - t0 - LAG_EVERY_S) * 1000))
