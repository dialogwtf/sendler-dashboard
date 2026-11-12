"""
Sendler Dash history before the live stream started - from ft_transfers (logic: services/dashboard/history.py):

- dash_candles: 1h and 1d for --days (1m - only the last 7 days, TTL removes older ones), volume in $ at the hourly
  NEAR rate from NEAR <-> USDC/USDT swaps, unique traders (signers);
- dash_trades: trades from DASH_TRADE_MIN_NEAR over the last 7 days (TTL), `_id` = `bf:<tx>:<wallet>`;
- dash_launches: Nearly / JustHoot launches from sniper_launch_stats and sniper_cap_watch (creator, launchpad, pool,
  ticker) with the price from the history's trades (px0, px, px_max, trades, volume), `backfill: true`.

Trades in pairs not against NEAR (PRL, QTC, TEAR, stablecoins) - at the pair asset's price of that hour
(history.QuoteHours). Launches after the boundary that the live stream did not write: with candles - the price from
the candles; without candles (pairs with PRL before 10 Nov, when the live stream did not understand such pairs) - a
second pass over ft_transfers from the boundary to now, for these tokens only: trades, candles (replaced) and price.

What cannot be restored: network (dash_network), big transfers (dash_moves), new items (dash_new), Intear launches,
tax and dev buy of launches.

The boundary is the first live trade (dash_trades without `bf:`), remembered in dash_state `backfill`: a re-run
takes the same boundary, candles before it are written by replacement (a re-run is safe), candles the live stream
has already started are topped up once (the list is in the same document). Live launches are not touched. A re-run
after a parsing fix removes what the recount no longer has: candles of the window closed a day before the boundary
(STALE_MARGIN_S - right at the boundary there are live candles), and history trades (`bf:`) - by the run mark `bf_run`.

Run it where Mongo is reachable:

    python scripts/backfill_dash.py --days 90 --dry-run   # count only
    python scripts/backfill_dash.py --days 90             # write

Reads ft_transfers day by day through the sender/receiver indexes: 90 days is about 7M rows, minutes.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReplaceOne, UpdateOne
from pymongo.errors import BulkWriteError

from config import Settings
from services.dashboard.history import (
    VENUE_NAMES,
    LaunchAgg,
    QuoteHours,
    TxLegs,
    UsdHours,
    agg_from_candles,
    history_via,
    launch_doc,
    leg_of,
    near_usd_quote,
    note_launch,
    settle_native,
    trade_from,
)
from services.dashboard.launches import SEED_QUOTES, pool_quote
from services.dashboard.prices import STABLE_DECIMALS
from services.dashboard.trades import CANDLE_TTL_S, INTERVALS, Candles, Trade, candle_doc, candle_op

DAY = 86400
SETTLE_S = 120  # legs of a transaction at the end of a day wait for the next day
STALE_MARGIN_S = DAY  # history candles closed at least this long before the boundary are ours only: extra ones are removed
STATE_ID = "backfill"


async def bulk(col, ops, *, ignore_dup: bool = False) -> int:
    n = 0
    for i in range(0, len(ops), 1000):
        try:
            r = await col.bulk_write(ops[i:i + 1000], ordered=False)
            n += r.upserted_count + r.modified_count
        except BulkWriteError as e:
            if not ignore_dup or any(x.get("code") != 11000 for x in e.details.get("writeErrors", [])):
                raise
            n += e.details.get("nUpserted", 0) + e.details.get("nModified", 0)
    return n


async def scan(db, start_s: float, end_s: float, usd: UsdHours, qh: QuoteHours, quotes: set[str],
               handle, label: str = "") -> int:
    """ft_transfers of the venues day by day -> the day's trades by time into handle(list[Trade]). First pass of a day -
    trades against NEAR (and the hour's rates: NEAR in $, pair asset prices), second - trades in other pairs at them."""
    rows = trades_n = 0
    venues = list(VENUE_NAMES)
    via = history_via(quotes, qh, usd, STABLE_DECIMALS)
    carry: dict[str, dict[str, TxLegs]] = {}
    t_start = time.time()
    day = start_s
    while day < end_s:
        end = min(day + DAY, end_s)
        txs: dict[str, dict[str, TxLegs]] = defaultdict(dict, carry)
        cur = db.ft_transfers.find(
            {"block_timestamp": {"$gte": dt.datetime.fromtimestamp(day, dt.timezone.utc),
                                 "$lt": dt.datetime.fromtimestamp(end, dt.timezone.utc)},
             "$or": [{"sender_id": {"$in": venues}}, {"receiver_id": {"$in": venues}}]},
            {"_id": 0, "ft_contract": 1, "sender_id": 1, "receiver_id": 1, "amount": 1, "tx_hash": 1,
             "signer_id": 1, "block_height": 1, "block_timestamp": 1})
        async for r in cur.batch_size(5000):
            rows += 1
            got = leg_of(r)
            if got is None:
                continue
            wallet, leg = got
            ts = r["block_timestamp"].timestamp()
            t = txs[r["tx_hash"]].get(wallet)
            if t is None:
                t = txs[r["tx_hash"]][wallet] = TxLegs(ts, int(r.get("block_height") or 0), r.get("signer_id"))
            t.ts = min(t.ts, ts)
            t.venues.add(leg.venue)
            t.legs.append(leg)
        carry = {}
        done: list[Trade] = []
        pending = []
        for tx, by_wallet in txs.items():
            last = max(t.ts for t in by_wallet.values())
            if end < end_s and last >= end - SETTLE_S:
                carry[tx] = by_wallet
                continue
            settle_native(by_wallet)
            for wallet, t in by_wallet.items():
                px = near_usd_quote(t)
                if px:
                    usd.add(t.ts, px)
                tr = trade_from(wallet, tx, t)
                if tr is not None:
                    done.append(tr)
                else:
                    pending.append((wallet, tx, t))
        for tr in done:
            if tr.token in quotes:
                qh.add(tr.token, tr.ts, tr.px)
        usd.build()
        qh.build()
        for wallet, tx, t in pending:
            tr = trade_from(wallet, tx, t, via)
            if tr is not None:
                done.append(tr)
        done.sort(key=lambda x: x.ts)
        trades_n += len(done)
        handle(done)
        day = end
        print(f"  {label}{dt.datetime.fromtimestamp(day, dt.timezone.utc):%m-%d %H:%M}: rows {rows}, "
              f"trades {trades_n} · {time.time() - t_start:.0f} s", flush=True)
    return trades_n


def fill_usd(candles: Candles, usd: UsdHours) -> None:
    """Volume in $: 1m and 1h - at the rate of their hour, 1d - the sum of its hours."""
    hourly_usd: dict[tuple[str, int], float] = defaultdict(float)
    for (token, name, start), b in candles.bars.items():
        rate = usd.at(start)
        if name in ("1m", "1h") and rate:
            b.v_usd = b.v * rate
        if name == "1h" and b.v_usd is not None:
            hourly_usd[(token, start // DAY * DAY)] += b.v_usd
    for (token, name, start), b in candles.bars.items():
        if name == "1d" and (token, start) in hourly_usd:
            b.v_usd = hourly_usd[(token, start)]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=90)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    db = AsyncIOMotorClient(Settings.MONGO_URI, tz_aware=True)[Settings.MONGO_DB_NAME]
    now = dt.datetime.now(dt.timezone.utc)

    state = await db.dash_state.find_one({"_id": STATE_ID}) or {}
    if not a.dry_run and state.get("days") and a.days < float(state["days"]):
        # launches are written by replacement from the window's trades: a shorter window would lose old launches' price
        sys.exit(f"--days {a.days:g} is less than on the first load ({float(state['days']):g}): run with "
                 f"--days {float(state['days']):g} (or --dry-run)")
    if state.get("until"):
        until = state["until"]
    else:
        live = await db.dash_trades.find_one({"_id": {"$not": {"$regex": "^bf:"}}}, {"ts": 1}, sort=[("ts", 1)])
        until = live["ts"] if live else now
    until_s = until.timestamp()
    since_s = until_s - a.days * DAY
    minute_from = max(since_s, now.timestamp() - CANDLE_TTL_S["1m"])
    trades_from = now.timestamp() - Settings.DASH_TRADES_TTL_DAYS * DAY
    print(f"window: {dt.datetime.fromtimestamp(since_s, dt.timezone.utc):%Y-%m-%d %H:%M} - {until:%Y-%m-%d %H:%M} UTC "
          f"(the live stream takes over from there)")

    stats = {d["_id"]: d async for d in db.sniper_launch_stats.find({})}
    caps = {d["_id"]: d async for d in db.sniper_cap_watch.find({}, {"fired_by": 0, "muted": 0})}
    launch_tokens = set(stats) | set(caps)
    print(f"launches in sniper_launch_stats: {len(stats)}, in sniper_cap_watch: {len(caps)}, total {len(launch_tokens)}")

    quotes = set(SEED_QUOTES)
    for t in launch_tokens:
        q = pool_quote(t, (stats.get(t) or {}).get("pool") or (caps.get(t) or {}).get("pool"))
        if q:
            quotes.add(q)
    print(f"pair assets other than NEAR: {len(quotes)} ({', '.join(sorted(quotes)[:6])}…)")

    candles = Candles()
    usd = UsdHours()
    qh = QuoteHours()
    aggs: dict[str, LaunchAgg] = {}
    recent: list[Trade] = []  # trades of the last days for dash_trades
    n_via = 0

    def take(day_trades: list[Trade], keep: set[str] | None = None, into: Candles = candles) -> None:
        nonlocal n_via
        for tr in day_trades:
            if keep is not None and tr.token not in keep:
                continue
            n_via += tr.via is not None
            into.add(tr, intervals=("1m", "1h", "1d") if tr.ts >= minute_from else ("1h", "1d"))
            if tr.token in launch_tokens:
                note_launch(aggs, tr)
            if tr.ts >= trades_from and tr.near >= Settings.DASH_TRADE_MIN_NEAR:
                recent.append(tr)

    trades_n = await scan(db, since_s, until_s, usd, qh, quotes, take)
    fill_usd(candles, usd)
    print(f"trades: {trades_n} (through a pair asset: {n_via}), candles: {len(candles)}, hours with a NEAR rate: {len(usd)}, "
          f"trades for the feed: {len(recent)}, launches with a price: {len(aggs)}")

    full, overlap = [], []
    done_overlap = set(state.get("overlap_done") or [])
    for (token, name, start), b in candles.bars.items():
        cid = f"{token}:{name}:{start}"
        if start + INTERVALS[name] <= until_s:
            full.append(ReplaceOne({"_id": cid}, candle_doc(token, name, start, b, now), upsert=True))
        elif cid not in done_overlap:
            overlap.append((cid, candle_op(token, name, start, b.o, b.h, b.l, b.c, b.v, b.buys, b.sells, b.wallets,
                                           b.v_usd, now, close=False)))
    metas = {d["_id"]: d async for d in db.ft_metadata.find({"_id": {"$in": list(launch_tokens)}},
                                                              {"symbol": 1, "name": 1})}
    # launches after the boundary that have no live document (the live stream did not write them): price from candles
    live = {d["_id"] async for d in db.dash_launches.find({"_id": {"$in": list(launch_tokens)},
                                                           "backfill": {"$ne": True}}, {"_id": 1})}
    late, bare = 0, {}  # bare: a launch after the boundary without a single candle -> its start time
    for t in launch_tokens:
        st = stats.get(t) or caps.get(t) or {}
        started = st.get("started_at") or st.get("t0")
        if t in aggs or t in live or not isinstance(started, (int, float)) or started < until_s:
            continue
        name = "1m" if started >= now.timestamp() - CANDLE_TTL_S["1m"] else "1h"
        rows = [c async for c in db.dash_candles.find({"token": t, "i": name}, {"o": 1, "h": 1, "c": 1, "t": 1, "v": 1,
                                                                               "buys": 1, "sells": 1, "updated_at": 1})]
        agg = agg_from_candles(rows)
        if agg:
            aggs[t] = agg
            late += 1
        elif not await db.dash_candles.count_documents({"token": t}, limit=1):
            bare[t] = float(started)
    print(f"launches after the boundary priced from candles: {late}, without candles: {len(bare)}")
    late_candles = Candles()
    if bare:
        # second pass: from the start of the first such launch to now, their tokens only (they have no candles)
        start = max(until_s, min(bare.values()) - SETTLE_S) // 3600 * 3600
        await scan(db, start, now.timestamp() - SETTLE_S, usd, qh, quotes,
                   lambda d: take(d, keep=set(bare), into=late_candles), label="after the boundary ")
        fill_usd(late_candles, usd)
        priced = {tok for tok in bare if tok in aggs}
        print(f"after the boundary: priced {len(priced)} of {len(bare)}, candles {len(late_candles)}")
        for tok in sorted(priced):
            a_ = aggs[tok]
            print(f"    {tok}: trades {a_.trades}, volume {a_.volume:.2f} Ⓝ, growth ×{a_.px_max / a_.px0:.1f}")
        if set(bare) - priced:
            print(f"    no trades: {', '.join(sorted(set(bare) - priced))}")
    launches = [d for d in (launch_doc(t, stats.get(t), caps.get(t), metas.get(t), aggs.get(t))
                            for t in launch_tokens) if d]
    print(f"to write: candles {len(full)} by replacement + {len(overlap)} topped up, trades {len(recent)}, "
          f"launches {len(launches)}")
    if a.dry_run:
        return

    if not state:
        await db.dash_state.update_one({"_id": STATE_ID}, {"$setOnInsert": {"until": until, "days": a.days,
                                                                            "at": now}}, upsert=True)
    n = await bulk(db.dash_candles, full)
    if late_candles.bars:  # these tokens had no candles - replacement (a re-run recounts them in full)
        n += await bulk(db.dash_candles, [ReplaceOne({"_id": f"{tok}:{name}:{start}"},
                                                     candle_doc(tok, name, start, b, now), upsert=True)
                                          for (tok, name, start), b in late_candles.bars.items()])
    if overlap:
        await bulk(db.dash_candles, [op for _, op in overlap])
        await db.dash_state.update_one({"_id": STATE_ID}, {"$addToSet": {"overlap_done": {
            "$each": [cid for cid, _ in overlap]}}})
    ttl = Settings.DASH_TRADES_TTL_DAYS
    # `bf:` is written by the history only - by replacement (a re-run after a parsing fix corrects the price); the run mark is for cleanup
    nt = await bulk(db.dash_trades, [ReplaceOne({"_id": t.key}, {
        **t.doc(ttl, None), "usd": t.near * r if (r := usd.at(t.ts)) else None, "backfill": True, "bf_run": now},
        upsert=True) for t in recent])
    gone_t = (await db.dash_trades.delete_many({"_id": {"$regex": "^bf:"}, "bf_run": {"$ne": now}})).deleted_count
    gone_c = 0
    for name, step in INTERVALS.items():
        if name == "1m" and minute_from >= until_s:
            continue
        lo = minute_from if name == "1m" else since_s
        hi = until_s - STALE_MARGIN_S - step  # start of a candle closed STALE_MARGIN_S before the boundary
        if hi <= lo:
            continue
        r = await db.dash_candles.delete_many({"i": name, "updated_at": {"$lt": now},
                                               "t": {"$gte": dt.datetime.fromtimestamp(lo, dt.timezone.utc),
                                                     "$lte": dt.datetime.fromtimestamp(hi, dt.timezone.utc)}})
        gone_c += r.deleted_count
    print(f"removed as stale: candles {gone_c}, history trades {gone_t}")
    # a live launch document is not touched: only a missing or our own (backfill) document matches
    nl = await bulk(db.dash_launches, [ReplaceOne({"_id": d["_id"], "backfill": True}, d, upsert=True)
                                       for d in launches], ignore_dup=True)
    # "Buy in the bot" link codes for every token of the history (as the worker does for new ones)
    from services.dashboard.botlinks import token_code

    tokens = ({token for token, _, _ in candles.bars} | {d["_id"] for d in launches}
              | {d["creator"] for d in launches if d.get("creator")})  # creators - for "Watch" (ww_)
    nc = await bulk(db.sniper_token_links, [UpdateOne({"_id": token_code(t)}, {"$setOnInsert": {"token": t, "at": now}},
                                                      upsert=True) for t in tokens])
    print(f"bot link codes: {nc} new of {len(tokens)}")
    await db.dash_state.update_one({"_id": STATE_ID}, {"$set": {"last_run": now, "candles": len(full),
                                                                "trades": len(recent), "launches": len(launches)}})
    print(f"written: candles {n + len(overlap)}, trades {nt}, launches {nl}")


if __name__ == "__main__":
    asyncio.run(main())
