"""Sendler Dash history before the live stream started - from an indexer's ft_transfers (pure logic, no database, no network).

A swap in ft_transfers is the transfers of one transaction between a wallet and a venue (Rhea DCL, Rhea v1): a buy -
wNEAR wallet -> venue and token venue -> wallet, a sell - the reverse; a route through USDC and other assets is
netted (as for smart money, services/dashboard/smart.trade_of). Legs without a pair - DCL orders, Claim, liquidity -
do not become a trade.

Differences from the live stream: Intear swaps from the internal balance and Rhea v1 swaps from internal deposits
are not in ft_transfers; round-trip arbitrage NEAR -> X -> NEAR nets to zero and misses the candles (the live stream
writes it to candles); pool and receipt are unknown. NEAR in $ - the hour's median of NEAR <-> USDC/USDT swaps.

Native NEAR (`ft_contract: "near"`, a Transfer action) is not wNEAR. Venue -> wallet is a swap's proceeds only when
the venue unwrapped that much wNEAR in the same transaction (Transfer wrap.near -> venue): that is how DCL and Rhea
v1 pay out a sale in NEAR. The rest is a storage deposit refund: JustHoot v2 on pool creation (`v2-pool-creator`
gets 0.0126 Ⓝ and hands the DCL the whole supply) would otherwise look like a "sale" of the whole supply for
pennies - px0 10⁵ times below the market. Wallet -> venue in native NEAR is a storage payment, not a swap.

Pairs not against NEAR (as in the live stream, trades.trade_of `via`): "gave the pair asset (PRL, QTC, a stable…) -
got the token" is a trade in NEAR at the asset's price of that hour: the median of the asset's trades against NEAR
in the same history (QuoteHours, no further than QUOTE_GAP_S), a stable - $1 at the hour's NEAR rate. The script
counts them in the day's second pass, when the hour's prices are known.

Script: scripts/backfill_dash.py.
"""
from __future__ import annotations

import datetime as dt
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

from services.chain.block import DCL, REF, STABLES, WNEAR
from services.chain.swaps import NEAR, QUOTES, Leg, net_flow

from .trades import YOCTO, Trade

VENUE_NAMES = {DCL: "Rhea DCL", REF: "Rhea"}
HOUR = 3600
QUOTE_GAP_S = 6 * HOUR  # a pair asset price further from the trade - the trade is not counted (like QUOTE_FRESH_S in the live stream)
NATIVE = "near"  # ft_contract of native NEAR in ft_transfers
NATIVE_LEG = "_native"  # a leg of native NEAR from a venue until matched with the wNEAR unwrap (settle_native)
UNWRAP = "_unwrap"  # transaction key instead of a wallet: how much wNEAR the venues unwrapped


@dataclass
class TxLegs:
    """Legs of one transaction of one wallet relative to the venues."""
    ts: float
    height: int
    signer: str | None
    venues: set[str] = field(default_factory=set)
    legs: list[Leg] = field(default_factory=list)


def leg_of(row: dict) -> tuple[str, Leg] | None:
    """An ft_transfers row -> (wallet, leg). None - not a transfer between a wallet and a venue."""
    token, frm, to = row.get("ft_contract"), row.get("sender_id"), row.get("receiver_id")
    try:
        amount = int(row.get("amount") or 0)
    except (TypeError, ValueError):
        return None
    if not token or amount <= 0 or not row.get("tx_hash"):
        return None
    if token == NATIVE:
        if frm == WNEAR and to in VENUE_NAMES:
            return UNWRAP, Leg("_venue", 0, NATIVE_LEG, amount, VENUE_NAMES[to])
        if frm in VENUE_NAMES and to and to not in VENUE_NAMES:
            return to, Leg("_venue", 0, NATIVE_LEG, amount, VENUE_NAMES[frm])
        return None  # storage payment and the like - not a swap leg
    asset = NEAR if token == WNEAR else token
    if to in VENUE_NAMES and frm not in VENUE_NAMES and frm:
        return frm, Leg(asset, amount, "_venue", 0, VENUE_NAMES[to])
    if frm in VENUE_NAMES and to not in VENUE_NAMES and to:
        return to, Leg("_venue", 0, asset, amount, VENUE_NAMES[frm])
    return None


def settle_native(by_wallet: dict[str, "TxLegs"]) -> None:
    """Legs of one transaction: native NEAR from a venue -> NEAR within the unwrapped wNEAR, the rest (a storage deposit
    refund) is dropped; the UNWRAP entry is removed. Call when all the transaction's legs are collected."""
    unwrap = by_wallet.pop(UNWRAP, None)
    budget = sum(leg.amount_out for leg in unwrap.legs) if unwrap else 0
    for t in by_wallet.values():
        if not any(leg.token_out == NATIVE_LEG for leg in t.legs):
            continue
        out = []
        for leg in t.legs:
            if leg.token_out != NATIVE_LEG:
                out.append(leg)
                continue
            take = min(leg.amount_out, budget)
            budget -= take
            if take > 0:
                out.append(Leg(leg.token_in, leg.amount_in, NEAR, take, leg.venue))
        t.legs = out


HistoryVia = Callable[[str, float], "float | None"]  # (asset, time) -> NEAR per raw unit of the pair asset | None


def trade_from(wallet: str, tx: str, t: TxLegs, via: HistoryVia | None = None) -> Trade | None:
    """Net flow of the legs -> a token trade in NEAR (like trades.trade_of in the live stream): NEAR <-> token, and with
    via also pair asset <-> token at the asset's price at the trade's time."""
    got, gave = net_flow(t.legs)
    got = [(a, n) for a, n in got if n > 0 and a != "_venue"]
    gave = [(a, n) for a, n in gave if n > 0 and a != "_venue"]
    if len(got) != 1 or len(gave) != 1:
        return None
    (a_in, n_in), (a_out, n_out) = got[0], gave[0]
    q = None
    if a_out == NEAR and a_in not in QUOTES:
        side, near, token, tok = "buy", n_out / YOCTO, a_in, n_in
    elif a_in == NEAR and a_out not in QUOTES:
        side, near, token, tok = "sell", n_in / YOCTO, a_out, n_out
    elif via is not None and NEAR not in (a_in, a_out):
        p_gave, p_got = via(a_out, t.ts), via(a_in, t.ts)
        if p_gave is not None and p_got is None:
            side, near, token, tok, q = "buy", n_out * p_gave, a_in, n_in, a_out
        elif p_got is not None and p_gave is None:
            side, near, token, tok, q = "sell", n_in * p_got, a_out, n_out, a_in
        else:
            return None
    else:
        return None
    if near <= 0 or tok <= 0:
        return None
    venue = next(iter(t.venues)) if len(t.venues) == 1 else "Rhea"
    return Trade(f"bf:{tx}:{wallet}", token, side, near, tok, near / tok, t.ts, t.height, tx, None, venue, None,
                 wallet, t.signer, False, q)


def near_usd_quote(t: TxLegs) -> float | None:
    """A NEAR <-> stable swap -> the NEAR rate in $; otherwise None."""
    got, gave = net_flow(t.legs)
    got = [(a, n) for a, n in got if n > 0 and a != "_venue"]
    gave = [(a, n) for a, n in gave if n > 0 and a != "_venue"]
    if len(got) != 1 or len(gave) != 1:
        return None
    pair = {got[0][0]: got[0][1], gave[0][0]: gave[0][1]}
    stable = next((a for a in pair if a in STABLES), None)
    if NEAR not in pair or stable is None:
        return None
    near = pair[NEAR] / YOCTO
    usd = pair[stable] / 10 ** STABLES[stable]
    if near < 0.5:  # dust - rounding noise
        return None
    px = usd / near
    return px if 0.1 < px < 1000 else None


class UsdHours:
    """NEAR in $ by hour: the median of the hour's swaps, an empty hour - the nearest past one (else the nearest future)."""

    def __init__(self) -> None:
        self._samples: dict[int, list[float]] = defaultdict(list)
        self._hours: list[int] = []
        self._px: dict[int, float] = {}

    def add(self, ts: float, px: float) -> None:
        self._samples[int(ts // HOUR)].append(px)

    def build(self) -> None:
        self._px = {h: statistics.median(v) for h, v in self._samples.items() if v}
        self._hours = sorted(self._px)

    def at(self, ts: float, max_gap_s: float | None = None) -> float | None:
        if not self._hours:
            return None
        h = int(ts // HOUR)
        if h in self._px:
            return self._px[h]
        import bisect

        i = bisect.bisect_right(self._hours, h)
        hh = self._hours[i - 1] if i else self._hours[0]
        if max_gap_s is not None and abs(hh - h) * HOUR > max_gap_s:
            return None
        return self._px[hh]

    def __len__(self) -> int:
        return len(self._px)


class QuoteHours:
    """Pair asset prices in NEAR by hour: the median of the asset's trades against NEAR (NEAR per raw unit)."""

    def __init__(self) -> None:
        self._by: dict[str, UsdHours] = defaultdict(UsdHours)

    def add(self, asset: str, ts: float, px: float) -> None:
        self._by[asset].add(ts, px)

    def build(self) -> None:
        for h in self._by.values():
            h.build()

    def at(self, asset: str, ts: float) -> float | None:
        h = self._by.get(asset)
        return h.at(ts, QUOTE_GAP_S) if h else None


def history_via(quotes: set[str], qh: QuoteHours, usd: UsdHours,
                stables: dict[str, int]) -> HistoryVia:
    """via for trade_from: a stable - $1 at the hour's NEAR rate, a launch pair asset - by QuoteHours; else None."""
    def via(a: str, ts: float) -> float | None:
        if a in stables:
            rate = usd.at(ts)
            return (1.0 / 10 ** stables[a]) / rate if rate else None
        return qh.at(a, ts) if a in quotes else None
    return via


@dataclass
class LaunchAgg:
    px0: float
    first: float
    px: float
    last: float
    px_max: float
    trades: int = 0
    volume: float = 0.0


def note_launch(aggs: dict[str, LaunchAgg], t: Trade) -> None:
    """Trades of a launch token (in time order): first, last and highest price, count and volume."""
    a = aggs.get(t.token)
    if a is None:
        a = aggs[t.token] = LaunchAgg(t.px, t.ts, t.px, t.ts, t.px)
    a.px, a.last, a.px_max = t.px, t.ts, max(a.px_max, t.px)
    a.trades += 1
    a.volume += t.near


def agg_from_candles(candles: list[dict]) -> LaunchAgg | None:
    """A launch's price from the dashboard's candles (in time order): the first open - px0, the last close - px, the
    highest high, trades and volume. For launches after the history boundary that the live stream did not write
    (9-10 Nov: Nearly links broke the write) - their trades and candles were written."""
    rows = [c for c in candles if c.get("o") and c.get("t")]
    if not rows:
        return None
    rows.sort(key=lambda c: c["t"])

    def ts(x):
        return (x if x.tzinfo else x.replace(tzinfo=dt.timezone.utc)).timestamp()

    last = rows[-1]
    a = LaunchAgg(float(rows[0]["o"]), ts(rows[0]["t"]), float(last.get("c") or last["o"]),
                  ts(last.get("updated_at") or last["t"]), max(float(c.get("h") or c["o"]) for c in rows))
    a.trades = sum(int(c.get("buys") or 0) + int(c.get("sells") or 0) for c in rows)
    a.volume = sum(float(c.get("v") or 0.0) for c in rows)
    return a


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x > 0 else None


def launch_doc(token: str, stat: dict | None, cap: dict | None, meta: dict | None,
               agg: LaunchAgg | None) -> dict | None:
    """A dash_launches document from what there is: sniper_launch_stats (creator, launchpad, pool, start of trading),
    sniper_cap_watch (ticker), ft_metadata, the history's trades. Stage - ready if there were trades, else started.
    None - neither creator nor launchpad is known."""
    stat, cap, meta = stat or {}, cap or {}, meta or {}
    pad = stat.get("launchpad") or cap.get("launchpad")
    creator = stat.get("creator") or cap.get("creator")
    if not pad or not creator:
        return None
    started = _f(stat.get("started_at")) or _f(cap.get("started_at"))
    t0 = _f(stat.get("t0")) or (agg.first if agg else None)
    first = min(x for x in (started, t0) if x) if (started or t0) else None
    if first is None:
        return None
    at = lambda x: dt.datetime.fromtimestamp(x, dt.timezone.utc)  # noqa: E731
    doc = {"_id": token, "token": token, "pad": pad, "creator": creator, "t_first": at(first), "t_started": at(first),
           "stage_n": 5 if (t0 or agg) else 1, "backfill": True, "updated_at": at(first)}
    if t0 or agg:
        doc["t_ready"] = at(t0 or agg.first)
    symbol = meta.get("symbol") or cap.get("symbol")
    for k, v in (("symbol", symbol), ("name", meta.get("name")), ("pool", stat.get("pool") or cap.get("pool"))):
        if v:
            doc[k] = v
    if agg:
        doc.update(px0=agg.px0, first_trade=at(agg.first), px=agg.px, last_trade=at(agg.last), px_max=agg.px_max,
                   trades=agg.trades, volume=agg.volume)
    return doc
