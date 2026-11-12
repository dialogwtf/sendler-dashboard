"""Rebuilds every number and chart of THESIS.md.

    python thesis/build.py                 # charts and the numbers table from the committed snapshot (no database)
    python thesis/build.py --collect       # recompute the snapshot from a Sendler Dash database, then the same

`--collect` reads the collections the indexer of this repository writes (MONGO_URI / MONGO_DB_NAME, as in
indexer/.env) and overwrites thesis/data/snapshot.json. The snapshot holds aggregates only - no wallet addresses.
Charts need matplotlib (thesis/requirements.txt); without it the numbers are still rebuilt.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
SNAPSHOT = HERE / "data" / "snapshot.json"
CHARTS = HERE / "charts"
NUMBERS = HERE / "data" / "numbers.md"
UTC = dt.timezone.utc
SUPPLY = 10 ** 9  # tokens per Nearly / JustHoot launch
LAUNCHPAD_SUFFIXES = (".nearlytrade.near", ".justhoot.near", ".launch.intear.near", ".launch-v2.intear.near")
MIN_TOKEN_NEAR = 0.1  # a wallet's entry into a token below this is noise (same threshold as the indexer's scoring)


def _day(t: dt.datetime) -> str:
    return (t if t.tzinfo else t.replace(tzinfo=UTC)).astimezone(UTC).strftime("%Y-%m-%d")


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def _share(n: int, total: int) -> float | None:
    return round(n / total, 4) if total else None


# ── collection ──────────────────────────────────────────────────────────────

def dex_daily(db) -> list[dict]:
    """DEX trading per UTC day from the daily candles: volume, trades, tokens, unique traders; launch tokens apart."""
    launch_tokens = {d["_id"] for d in db.dash_launches.find({}, {"_id": 1})}

    def is_launch(token: str) -> bool:  # launches older than dash_launches are recognised by the launchpad's account
        return token in launch_tokens or token.endswith(LAUNCHPAD_SUFFIXES)

    days: dict[str, dict] = {}
    wallets: dict[str, set] = defaultdict(set)
    cur = db.dash_candles.find({"i": "1d"}, {"token": 1, "t": 1, "v": 1, "v_usd": 1, "buys": 1, "sells": 1, "w": 1})
    for c in cur:
        day = _day(c["t"])
        r = days.setdefault(day, {"day": day, "near": 0.0, "usd": 0.0, "trades": 0, "tokens": 0,
                                  "launch_near": 0.0, "launch_tokens": 0})
        v = float(c.get("v") or 0)
        r["near"] += v
        r["usd"] += float(c.get("v_usd") or 0)
        r["trades"] += int(c.get("buys") or 0) + int(c.get("sells") or 0)
        r["tokens"] += 1
        if is_launch(c["token"]):
            r["launch_near"] += v
            r["launch_tokens"] += 1
        wallets[day].update(c.get("w") or ())
    out = []
    for day in sorted(days):
        r = days[day]
        r["traders"] = len(wallets[day])
        out.append({k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()})
    return out


def token_concentration(db, days: int = 30) -> dict:
    """Share of DEX volume taken by the largest tokens over the last `days`."""
    since = dt.datetime.now(UTC) - dt.timedelta(days=days)
    vol: Counter = Counter()
    for c in db.dash_candles.find({"i": "1d", "t": {"$gte": since}}, {"token": 1, "v": 1}):
        vol[c["token"]] += float(c.get("v") or 0)
    total = sum(vol.values())
    top = vol.most_common(10)
    launch = sum(v for t, v in vol.items() if t.endswith(LAUNCHPAD_SUFFIXES))
    return {"days": days, "tokens": len(vol), "near": round(total, 1), "launchpad_share": _share_f(launch, total),
            "launchpad_tokens": sum(t.endswith(LAUNCHPAD_SUFFIXES) for t in vol),
            "top1_share": _share_f(top[0][1] if top else 0, total),
            "top5_share": _share_f(sum(v for _, v in top[:5]), total),
            "top10_share": _share_f(sum(v for _, v in top[:10]), total),
            "top10": [{"token": t, "near": round(v, 1), "share": _share_f(v, total)} for t, v in top]}


def _share_f(x: float, total: float) -> float | None:
    return round(x / total, 4) if total else None


def launches(db, near_usd: float) -> dict:
    """Outcomes of launchpad launches: do they trade, how far do they run, are they alive, who creates them."""
    now = dt.datetime.now(UTC)
    decimals = {}
    docs = list(db.dash_launches.find({}, {"pad": 1, "t_first": 1, "creator": 1, "px0": 1, "px": 1, "px_max": 1,
                                           "trades": 1, "volume": 1, "last_trade": 1, "backfill": 1, "fee_farm": 1,
                                           "extra.dev_buy": 1, "extra.tax": 1, "extra.fee_pips": 1}))
    for m in db.ft_metadata.find({"_id": {"$in": [d["_id"] for d in docs]}}, {"decimals": 1}):
        decimals[m["_id"]] = m.get("decimals")
    per_day: dict[str, Counter] = defaultdict(Counter)
    by_pad: Counter = Counter()
    creators: Counter = Counter()
    traded, x_max, x_now, vols, peak_caps, alive_7d, old = 0, [], [], [], [], 0, 0
    live_n = live_farm = live_no_dev_buy = 0
    for d in docs:
        t = d["t_first"] if d["t_first"].tzinfo else d["t_first"].replace(tzinfo=UTC)
        pad = d.get("pad") or "?"
        per_day[_day(t)][pad] += 1
        by_pad[pad] += 1
        if d.get("creator"):
            creators[d["creator"]] += 1
        if not d.get("backfill"):
            live_n += 1
            live_farm += bool(d.get("fee_farm"))
            dev_buy = (d.get("extra") or {}).get("dev_buy")
            live_no_dev_buy += dev_buy is not None and str(dev_buy) in ("0", "0.0")
        px0, px, pm = d.get("px0"), d.get("px"), d.get("px_max")
        if not px0:
            continue
        traded += 1
        vols.append(float(d.get("volume") or 0))
        if pm:
            x_max.append(pm / px0)
        if px:
            x_now.append(px / px0)
        dec = decimals.get(d["_id"])
        if pm and dec is not None and pad in ("nearly", "justhoot"):
            peak_caps.append(pm * SUPPLY * 10 ** int(dec) * near_usd)
        if (now - t).days >= 7:
            old += 1
            lt = d.get("last_trade")
            if lt and (now - (lt if lt.tzinfo else lt.replace(tzinfo=UTC))).days < 3:
                alive_7d += 1
    n = len(docs)
    top = creators.most_common(10)
    first = min((d["t_first"] for d in docs), default=None)
    return {
        "from": _day(first) if first else None, "to": _day(now), "total": n, "by_pad": dict(by_pad),
        "per_day": [{"day": day, **per_day[day]} for day in sorted(per_day)],
        "traded": traded, "traded_share": _share(traded, n),
        "volume_near": {"median": _r(statistics.median(vols) if vols else None), "p90": _r(_pct(vols, 0.9)),
                        "total": _r(sum(vols)), "under_10_share": _share(sum(v < 10 for v in vols), len(vols)),
                        "over_1000_share": _share(sum(v >= 1000 for v in vols), len(vols))},
        "x_max": {"median": _r(statistics.median(x_max) if x_max else None, 3),
                  "ge2_share": _share(sum(x >= 2 for x in x_max), len(x_max)),
                  "ge5_share": _share(sum(x >= 5 for x in x_max), len(x_max)),
                  "ge10_share": _share(sum(x >= 10 for x in x_max), len(x_max))},
        "x_now": {"median": _r(statistics.median(x_now) if x_now else None, 3),
                  "below_start_share": _share(sum(x < 1 for x in x_now), len(x_now)),
                  "below_half_share": _share(sum(x < 0.5 for x in x_now), len(x_now)),
                  "above_2x_share": _share(sum(x >= 2 for x in x_now), len(x_now))},
        "peak_cap_usd": {"n": len(peak_caps), "near_usd": near_usd,
                         "median": _r(statistics.median(peak_caps) if peak_caps else None, 0),
                         "ge_10k_share": _share(sum(c >= 10_000 for c in peak_caps), len(peak_caps)),
                         "ge_100k_share": _share(sum(c >= 100_000 for c in peak_caps), len(peak_caps))},
        "alive": {"older_than_7d": old, "traded_in_last_3d": alive_7d, "share": _share(alive_7d, old)},
        "creators": {"unique": len(creators), "one_launch_share": _share(sum(c == 1 for c in creators.values()),
                                                                           len(creators)),
                     "ge5_launches": sum(c >= 5 for c in creators.values()),
                     "top10_launch_share": _share(sum(c for _, c in top), n),
                     "max_by_one": top[0][1] if top else 0},
        "live": {"n": live_n, "fee_farm": live_farm, "fee_farm_share": _share(live_farm, live_n),
                 "no_dev_buy": live_no_dev_buy, "no_dev_buy_share": _share(live_no_dev_buy, live_n)},
    }


def _r(x, nd: int = 2):
    return round(x, nd) if isinstance(x, (int, float)) else x


def creators_behaviour(db) -> dict:
    """What creators did with their own tokens (sniper_launch_stats): dumps within 5 minutes of trading."""
    docs = list(db.sniper_launch_stats.find({}, {"creator": 1, "launchpad": 1, "dumped": 1, "dev_in": 1, "dev_sold": 1,
                                                 "first_sell_s": 1, "started_at": 1}))
    judged = [d for d in docs if d.get("dumped") is not None]
    by_pad: dict[str, list] = defaultdict(list)
    by_week: dict[str, list] = defaultdict(list)
    per_creator: dict[str, list] = defaultdict(list)
    for d in judged:
        by_pad[d.get("launchpad") or "?"].append(bool(d["dumped"]))
        if d.get("started_at"):
            wk = dt.datetime.fromtimestamp(float(d["started_at"]), UTC)
            by_week[(wk - dt.timedelta(days=wk.weekday())).strftime("%Y-%m-%d")].append(bool(d["dumped"]))
        per_creator[d.get("creator") or "?"].append(bool(d["dumped"]))
    with_dev = [d for d in judged if int(d.get("dev_in") or 0) > 0]
    sold_any = [d for d in with_dev if int(d.get("dev_sold") or 0) > 0]
    first_sell = [float(d["first_sell_s"]) for d in docs if d.get("first_sell_s") is not None and d["first_sell_s"] >= 0]
    repeat = {c: v for c, v in per_creator.items() if len(v) >= 3}
    serial = sum(1 for v in repeat.values() if sum(v) / len(v) >= 0.5)
    return {
        "launches": len(docs), "judged": len(judged), "dumped": sum(bool(d["dumped"]) for d in judged),
        "dumped_share": _share(sum(bool(d["dumped"]) for d in judged), len(judged)),
        "by_pad": {p: {"n": len(v), "dumped_share": _share(sum(v), len(v))} for p, v in by_pad.items()},
        "by_week": [{"week": w, "n": len(v), "dumped_share": _share(sum(v), len(v))} for w, v in sorted(by_week.items())],
        "creator_held_tokens": len(with_dev), "creator_held_share": _share(len(with_dev), len(judged)),
        "dumped_share_of_holders": _share(sum(bool(d["dumped"]) for d in with_dev), len(with_dev)),
        "sold_any_share_of_holders": _share(len(sold_any), len(with_dev)),
        "first_sell_s": {"median": _r(statistics.median(first_sell) if first_sell else None, 0),
                         "within_60s_share": _share(sum(s <= 60 for s in first_sell), len(first_sell))},
        "repeat_creators": {"ge3_launches": len(repeat), "dump_half_or_more": serial,
                            "share": _share(serial, len(repeat))},
    }


def wallet_outcomes(db) -> dict:
    """Profit of trading wallets over the flows window (sniper_smart_flows): the matched part of every position plus
    the rest at the last price - the same formula the indexer uses to rank smart money."""
    prices = {d["_id"]: float(d.get("price") or 0) for d in db.sniper_smart_prices.find({}, {"price": 1})}
    pnl: dict[str, float] = defaultdict(float)
    invested: dict[str, float] = defaultdict(float)
    tokens: Counter = Counter()
    first = last = None
    for f in db.sniper_smart_flows.find({}, {"wallet": 1, "token": 1, "near_in": 1, "near_out": 1, "tok_in": 1,
                                             "tok_out": 1, "first_at": 1, "last_at": 1}):
        near_in, near_out = float(f.get("near_in") or 0), float(f.get("near_out") or 0)
        tok_in, tok_out = float(f.get("tok_in") or 0), float(f.get("tok_out") or 0)
        if f.get("first_at") and (first is None or f["first_at"] < first):
            first = f["first_at"]
        if f.get("last_at") and (last is None or f["last_at"] > last):
            last = f["last_at"]
        if near_in < MIN_TOKEN_NEAR or tok_in <= 0:
            continue
        m = min(tok_in, tok_out)
        realized = (near_out * m / tok_out if tok_out else 0.0) - near_in * m / tok_in
        left = tok_in - m
        held = left * prices.get(f["token"], 0.0) / 1e24 - near_in * left / tok_in
        w = f["wallet"]
        pnl[w] += realized + held
        invested[w] += near_in
        tokens[w] += 1
    vals = sorted(pnl.values())
    n = len(vals)
    winners = [v for v in vals if v > 0]
    losers = [v for v in vals if v < 0]
    top1 = sorted(winners, reverse=True)[: max(1, n // 100)]
    active = [w for w in pnl if tokens[w] >= 5]
    active_win = sum(pnl[w] > 0 for w in active)
    buckets = [(-1e18, -100), (-100, -10), (-10, -1), (-1, 0), (0, 1), (1, 10), (10, 100), (100, 1e18)]
    return {
        "from": _day(first) if first else None, "to": _day(last) if last else None,
        "wallets": n, "invested_near": _r(sum(invested.values()), 0),
        "profitable": len(winners), "profitable_share": _share(len(winners), n),
        "median_pnl_near": _r(statistics.median(vals) if vals else None, 3),
        "winners_total_near": _r(sum(winners), 0), "losers_total_near": _r(sum(losers), 0),
        "net_near": _r(sum(vals), 0),
        "top1pct_share_of_profit": _share_f(sum(top1), sum(winners)),
        "active_ge5_tokens": {"wallets": len(active), "profitable_share": _share(active_win, len(active))},
        "histogram": [{"from": a, "to": b, "wallets": sum(a <= v < b for v in vals)} for a, b in buckets],
    }


def network(db, near_usd: float) -> dict:
    """Network metrics per full UTC day (dash_network 1d)."""
    today = _day(dt.datetime.now(UTC))
    rows = []
    for d in db.dash_network.find({"i": "1d"}, {"hll": 0}).sort("t", 1):
        day = _day(d["t"])
        txs = int(d.get("txs") or 0)
        burnt = float(d.get("burnt_near") or 0)
        rows.append({"day": day, "full": day != today and int(d.get("blocks") or 0) > 100_000, "blocks": d.get("blocks"),
                     "txs": txs, "receipts": d.get("receipts"), "signers": d.get("signers"),
                     "new_accounts": d.get("new_accounts"), "burnt_near": _r(burnt, 2),
                     "fee_per_tx_usd": _r(burnt / txs * near_usd, 6) if txs else None,
                     "tps": _r(txs / 86400, 2)})
    return {"near_usd": near_usd, "days": rows}


def holders(db) -> dict:
    """Holder counts of launch tokens whose balances are exact (complete) or reconciled (seeded)."""
    counts = [int(d.get("holders") or 0) for d in db.dash_holder_stats.find(
        {"$or": [{"complete": True}, {"seeded": {"$exists": True}}]}, {"holders": 1})]
    return {"tokens": len(counts), "median": _r(statistics.median(counts) if counts else None, 0),
            "p90": _pct([float(c) for c in counts], 0.9),
            "le_10_share": _share(sum(c <= 10 for c in counts), len(counts)),
            "ge_50_share": _share(sum(c >= 50 for c in counts), len(counts)),
            "ge_100_share": _share(sum(c >= 100 for c in counts), len(counts)), "max": max(counts, default=0)}


def big_moves(db) -> dict:
    """Transfers of $1,000 and more (dash_moves, kept 30 days): by token and by the kind of counterparty."""
    first = db.dash_moves.find_one({"kind": "transfer"}, {"ts": 1}, sort=[("ts", 1)])
    last = db.dash_moves.find_one({"kind": "transfer"}, {"ts": 1}, sort=[("ts", -1)])
    if not first:
        return {}
    kinds = {d["_id"]: d.get("kind") for d in db.dash_labels.find({"kind": {"$in": ["exchange", "bridge", "hub"]}},
                                                                   {"kind": 1})}
    by_token: Counter = Counter()
    by_route: Counter = Counter()
    n = 0
    for m in db.dash_moves.find({"kind": "transfer"}, {"token": 1, "usd": 1, "from": 1, "to": 1}):
        usd = float(m.get("usd") or 0)
        n += 1
        by_token[m.get("token")] += usd
        a, b = kinds.get(m.get("from"), "wallet"), kinds.get(m.get("to"), "wallet")
        by_route["wallet to wallet" if a == b == "wallet" else "with a hub or exchange" if "bridge" not in (a, b)
                 else "with a bridge"] += usd
    total = sum(by_token.values())
    top = by_token.most_common(6)
    symbols = {d["_id"]: d.get("symbol") for d in db.ft_metadata.find({"_id": {"$in": [t for t, _ in top]}},
                                                                       {"symbol": 1})}
    hours = (last["ts"] - first["ts"]).total_seconds() / 3600
    return {"from": first["ts"].strftime("%Y-%m-%d %H:%M"), "to": last["ts"].strftime("%Y-%m-%d %H:%M"),
            "hours": round(hours, 1), "transfers": n, "usd": round(total), "usd_per_day": round(total / hours * 24),
            "by_token": [{"token": t, "symbol": "NEAR" if t == "near" else symbols.get(t), "usd": round(v),
                          "share": _share_f(v, total)} for t, v in top],
            "by_route": {k: _share_f(v, total) for k, v in by_route.most_common()}}


def launch_speed(db) -> dict:
    """Seconds from a launch's first on-chain signal to its first trade (launches seen live, all stages recorded)."""
    secs, ready = [], []
    for d in db.dash_launches.find({"backfill": {"$ne": True}, "t_started": {"$exists": True}},
                                   {"t_started": 1, "t_ready": 1, "first_trade": 1}):
        if d.get("first_trade"):
            secs.append((d["first_trade"] - d["t_started"]).total_seconds())
        if d.get("t_ready"):
            ready.append((d["t_ready"] - d["t_started"]).total_seconds())
    secs = [s for s in secs if s >= 0]
    ready = [s for s in ready if s >= 0]
    return {"launches": len(secs), "to_first_trade_s": {"median": _r(statistics.median(secs) if secs else None, 1),
                                                         "p90": _r(_pct(secs, 0.9), 1)},
            "to_ready_s": {"median": _r(statistics.median(ready) if ready else None, 1)}}


def near_price(db) -> list[dict]:
    """Daily NEAR/USD from the REF price snapshots, where the database has them (optional collection)."""
    if "ft_price_snapshots" not in db.list_collection_names():
        return []
    out = []
    for d in db.ft_price_snapshots.aggregate([
            {"$match": {"contract_id": "wrap.near"}},
            {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$ts"}}, "usd": {"$avg": "$price_usd"}}},
            {"$sort": {"_id": 1}}]):
        out.append({"day": d["_id"], "usd": round(float(d["usd"]), 4)})
    return out


def collect(db) -> dict:
    rate = db.ft_prices.find_one({"_id": "wrap.near"}, {"price_usd": 1}) or {}
    near_usd = float(rate.get("price_usd") or 0)
    return {
        "generated_at": dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "near_usd": near_usd,
        "dex_daily": dex_daily(db), "concentration": token_concentration(db),
        "launches": launches(db, near_usd), "creators": creators_behaviour(db),
        "wallets": wallet_outcomes(db), "network": network(db, near_usd), "holders": holders(db),
        "moves": big_moves(db), "launch_speed": launch_speed(db), "near_price": near_price(db),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--collect", action="store_true", help="recompute the snapshot from the database")
    args = ap.parse_args()
    if args.collect:
        import pymongo

        try:
            from dotenv import load_dotenv

            load_dotenv(HERE.parent / "indexer" / ".env")
        except ImportError:
            pass
        client = pymongo.MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"), tz_aware=True)
        snap = collect(client[os.getenv("MONGO_DB_NAME", "near_indexer")])
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps(snap, indent=1), encoding="utf-8")
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    from render import render  # charts and the numbers table

    render(snap, CHARTS, NUMBERS)
    print(f"snapshot of {snap['generated_at']}: numbers -> {NUMBERS.relative_to(HERE.parent)}, "
          f"charts -> {CHARTS.relative_to(HERE.parent)}")


if __name__ == "__main__":
    main()
