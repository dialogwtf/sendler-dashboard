"""Trades and candles from the stream's swaps - pure logic, no database, no network.

A trade is a swap where one side is NEAR/wNEAR and the other is not a quote asset (not a stable): the token's price
in NEAR. A pair not against NEAR (launches paired with PRL, QTC, TEAR, a stable…) - a swap of the token against a
pair asset that has a price in NEAR (`via`: asset -> NEAR per raw unit, or None): the asset amount is converted to
NEAR, the trade carries `via`. A swap of two pair assets (PRL <-> USDC) is not a trade.

The price is stored raw: NEAR per one raw unit of the token (`px`), without decimals - the worker needs neither
metadata nor RPC; the human-readable price is computed by the API, which has ft_metadata.

A round-trip arbitrage route (the receipt's first asset = the last: NEAR -> X -> NEAR) is not written to the trade
feed - it is not demand for the token but pool alignment; such swaps do go into candles: they really move the pool's price.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

from pymongo import UpdateOne

from services.chain.swaps import NEAR, QUOTES, BlockSwap

YOCTO = 10 ** 24
INTERVALS = {"1m": 60, "1h": 3600, "1d": 86400}
CANDLE_TTL_S = {"1m": 7 * 86400, "1h": 365 * 86400, "1d": None}


@dataclass(frozen=True)
class Trade:
    key: str  # receipt:index
    token: str
    side: str  # buy | sell - from the token's side: the token was bought for NEAR, or sold
    near: float  # NEAR in the trade
    tok: int  # raw token units
    px: float  # NEAR per raw unit
    ts: float
    height: int
    tx: str | None
    receipt: str | None
    venue: str
    pool: str | None
    wallet: str | None  # the swap's actor (for an aggregator - the aggregator)
    signer: str | None
    arb: bool = False
    via: str | None = None  # the pair asset NEAR was computed through (a pair not against NEAR); None - a swap against NEAR

    def doc(self, ttl_days: int, near_usd: float | None) -> dict:
        at = dt.datetime.fromtimestamp(self.ts, dt.timezone.utc)
        return {"_id": self.key, "ts": at, "block": self.height, "tx": self.tx, "receipt": self.receipt,
                "venue": self.venue, "pool": self.pool, "token": self.token, "side": self.side,
                "near": self.near, "usd": self.near * near_usd if near_usd else None, "tok": str(self.tok),
                "px": self.px, "wallet": self.wallet, "signer": self.signer, "via": self.via,
                "expires_at": at + dt.timedelta(days=ttl_days)}


Via = Callable[[str], "float | None"]


def trade_of(bs: BlockSwap, arb: bool = False, via: Via | None = None) -> Trade | None:
    """A swap -> a token trade in NEAR; None - not a trade (a stable against NEAR, two pair assets, no asset price, zeros)."""
    s = bs.swap
    q = None
    if s.token_in == NEAR and s.token_out not in QUOTES:
        side, near, token, tok = "buy", s.amount_in / YOCTO, s.token_out, s.amount_out
    elif s.token_out == NEAR and s.token_in not in QUOTES:
        side, near, token, tok = "sell", s.amount_out / YOCTO, s.token_in, s.amount_in
    elif via is not None and NEAR not in (s.token_in, s.token_out):
        p_in, p_out = via(s.token_in), via(s.token_out)
        if p_in is not None and p_out is None:
            side, near, token, tok, q = "buy", s.amount_in * p_in, s.token_out, s.amount_out, s.token_in
        elif p_out is not None and p_in is None:
            side, near, token, tok, q = "sell", s.amount_out * p_out, s.token_in, s.amount_in, s.token_out
        else:
            return None
    else:
        return None
    if near <= 0 or tok <= 0:
        return None
    return Trade(bs.key, token, side, near, tok, near / tok, bs.ts, bs.height, bs.tx_hash, bs.receipt_id,
                 s.venue, s.pool, s.actor, bs.signer, arb, q)


def arbitrage_receipts(swaps: list[BlockSwap]) -> set[str]:
    """Receipts with a round-trip route: >= 2 swaps, the first asset = the last."""
    by_receipt: dict[str | None, list[BlockSwap]] = defaultdict(list)
    for bs in swaps:
        by_receipt[bs.receipt_id].append(bs)
    out = set()
    for rid, group in by_receipt.items():
        if rid and len(group) >= 2 and group[0].swap.token_in == group[-1].swap.token_out:
            out.add(rid)
    return out


def block_trades(swaps: list[BlockSwap], via: Via | None = None) -> list[Trade]:
    """A block's trades with the arbitrage flag (via - as in trade_of; the live stream walks the swaps itself: a pair
    asset's price is updated after every swap - DashWorker._trades)."""
    arb = arbitrage_receipts(swaps)
    out = []
    for bs in swaps:
        t = trade_of(bs, bs.receipt_id in arb, via)
        if t is not None:
            out.append(t)
    return out


@dataclass
class _Bar:
    o: float
    h: float
    l: float  # noqa: E741 - low, as in OHLC
    c: float
    v: float = 0.0  # volume, NEAR
    buys: int = 0
    sells: int = 0
    wallets: set = field(default_factory=set)
    v_usd: float | None = None  # volume in $ at each trade's rate (history); None - at the rate on flush


class Candles:
    """Candles from trades: accumulates changes in memory, drain() returns them as one batch of UpdateOne.

    Writes are increments ($inc of volume and counters, $max/$min of extremes, $set of the close, the open - on insert),
    so a restart in the middle of a candle loses nothing. Trades come in block order: the close is the last price."""

    def __init__(self) -> None:
        self._bars: dict[tuple[str, str, int], _Bar] = {}
        self.last_px: dict[str, float] = {}  # the token's last price from a main pool trade

    def __len__(self) -> int:
        return len(self._bars)

    @property
    def bars(self) -> dict[tuple[str, str, int], _Bar]:
        """(token, interval, start) -> the candle until the flush (the history writes them itself)."""
        return self._bars

    def add(self, t: Trade, near_usd: float | None = None, intervals=INTERVALS, price: bool = True) -> None:
        """near_usd - the NEAR rate at the trade's time (history); the live stream does not pass it: volume in $ - on flush.
        price=False - a trade outside the token's main pool (MainPools): volume, counters and traders go into the candle,
        the price does not (a new candle opens at the main pool's last price, and without one - at the trade's price)."""
        if price:
            self.last_px[t.token] = t.px
        px = t.px if price else self.last_px.get(t.token, t.px)
        for name in intervals:
            step = INTERVALS[name]
            start = int(t.ts // step * step)
            key = (t.token, name, start)
            bar = self._bars.get(key)
            if bar is None:
                bar = self._bars[key] = _Bar(px, px, px, px)
            elif price:
                bar.h, bar.l, bar.c = max(bar.h, px), min(bar.l, px), px
            bar.v += t.near
            if near_usd:
                bar.v_usd = (bar.v_usd or 0.0) + t.near * near_usd
            if t.side == "buy":
                bar.buys += 1
            else:
                bar.sells += 1
            who = t.signer or t.wallet
            if who:
                bar.wallets.add(who)

    def drain(self, near_usd: float | None, now: dt.datetime | None = None) -> list[UpdateOne]:
        now = now or dt.datetime.now(dt.timezone.utc)
        ops = [candle_op(token, name, start, b.o, b.h, b.l, b.c, b.v, b.buys, b.sells, b.wallets,
                         b.v_usd if b.v_usd is not None else (b.v * near_usd if near_usd else None), now)
               for (token, name, start), b in self._bars.items()]
        self._bars.clear()
        return ops


MAIN_HALF_LIFE_S = 3600.0  # a pool's volume for choosing the main one halves every hour
MAIN_KEEP_TOKENS = 50_000  # tokens in memory; beyond that the oldest are forgotten


class MainPools:
    """A token's main pool is the one with the most NEAR traded recently (volume decays with half-life
    MAIN_HALF_LIFE_S): its trades set the price of candles, of the cap and of a launch's growth, while a swap in a thin
    pool does not, and one trade in an empty pool draws no false candle or "×10". Pool liquidity cannot be known without
    RPC - volume stands in for it; arbitrage counts: it happens where the liquidity is. After a restart the count starts from zero."""

    def __init__(self) -> None:
        self._pools: dict[str, dict[str, tuple[float, float]]] = {}  # token -> pool -> (NEAR volume, when)

    @staticmethod
    def _decayed(entry: tuple[float, float], ts: float) -> float:
        vol, at = entry
        return vol * 0.5 ** (max(0.0, ts - at) / MAIN_HALF_LIFE_S)

    def note(self, t: Trade) -> bool:
        """Count a trade; returns whether its pool is the token's main one (after counting this trade)."""
        pool = t.pool or f"?{t.venue}"
        pools = self._pools.get(t.token)
        if pools is None:
            if len(self._pools) >= MAIN_KEEP_TOKENS:
                for token in list(self._pools)[: MAIN_KEEP_TOKENS // 4]:
                    del self._pools[token]
            pools = self._pools[t.token] = {}
        pools[pool] = (self._decayed(pools.get(pool, (0.0, t.ts)), t.ts) + t.near, t.ts)
        best = max(pools, key=lambda p: self._decayed(pools[p], t.ts))
        return best == pool

    def main(self, token: str) -> str | None:
        pools = self._pools.get(token)
        if not pools:
            return None
        now = max(at for _, at in pools.values())
        return max(pools, key=lambda p: self._decayed(pools[p], now))


TRADERS_CAP = 20_000  # wallets per candle - the set stops growing beyond this (traders = "20000+")


def _add(field_: str, x: float) -> dict:
    return {"$add": [{"$ifNull": [f"${field_}", 0]}, x]}


def candle_op(token: str, name: str, start: int, o: float, h: float, low: float, c: float, v: float, buys: int,
              sells: int, wallets, v_usd: float | None, now: dt.datetime, *, close: bool = True) -> UpdateOne:
    """A candle increment as one update pipeline: open and expiry - only for a new one, extremes - max/min, volume and
    counters - by addition, wallets - by set union (`w`), `traders` - their number. The sum of increments does not
    depend on order or batching, so a restart in the middle of a candle loses nothing. close=False - leave the close
    alone (loading history into a candle the live stream already keeps: its close is newer)."""
    at = dt.datetime.fromtimestamp(start, dt.timezone.utc)
    lit = {"$literal": sorted(wallets)}
    s1 = {"token": token, "i": name, "t": at,
          "o": {"$ifNull": ["$o", o]} if close else o,
          "h": {"$max": [{"$ifNull": ["$h", h]}, h]}, "l": {"$min": [{"$ifNull": ["$l", low]}, low]},
          "v": _add("v", v), "buys": _add("buys", buys), "sells": _add("sells", sells), "updated_at": now,
          "w": {"$cond": [{"$gte": [{"$size": {"$ifNull": ["$w", []]}}, TRADERS_CAP]}, "$w",
                          {"$setUnion": [{"$ifNull": ["$w", []]}, lit]}]}}
    if close:
        s1["c"] = c
    else:
        s1["c"] = {"$ifNull": ["$c", c]}
    if v_usd is not None:
        s1["v_usd"] = _add("v_usd", v_usd)
    ttl = CANDLE_TTL_S[name]
    if ttl:
        s1["expires_at"] = {"$ifNull": ["$expires_at", at + dt.timedelta(seconds=ttl)]}
    return UpdateOne({"_id": f"{token}:{name}:{start}"}, [{"$set": s1}, {"$set": {"traders": {"$size": "$w"}}}],
                     upsert=True)


def candle_doc(token: str, name: str, start: int, b: _Bar, now: dt.datetime) -> dict:
    """A full candle document (history entirely before the live stream: written by replacement - a re-run is safe)."""
    at = dt.datetime.fromtimestamp(start, dt.timezone.utc)
    w = sorted(b.wallets)[:TRADERS_CAP]
    doc = {"_id": f"{token}:{name}:{start}", "token": token, "i": name, "t": at, "o": b.o, "h": b.h, "l": b.l,
           "c": b.c, "v": b.v, "buys": b.buys, "sells": b.sells, "w": w, "traders": len(w), "updated_at": now}
    if b.v_usd is not None:
        doc["v_usd"] = b.v_usd
    ttl = CANDLE_TTL_S[name]
    if ttl:
        doc["expires_at"] = at + dt.timedelta(seconds=ttl)
    return doc
