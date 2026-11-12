"""Rebuilds every number and chart of THESIS.md.

    python thesis/build.py                 # charts and the numbers table from the committed snapshot (no database)
    python thesis/build.py --collect       # recompute the snapshot from a Sendler Dash database, then the same

`--collect` reads the collections the indexer of this repository writes (MONGO_URI / MONGO_DB_NAME, as in
indexer/.env) and overwrites thesis/data/snapshot.json. The snapshot holds aggregates only - no wallet addresses.
Charts and numbers are plain Python; only `--collect` needs pymongo (thesis/requirements.txt). On a database with
no launches or no wallet positions yet, the parts of the thesis about them are left out of the output.
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
LOCAL = HERE / "data" / "snapshot.local.json"  # a collected snapshot too short for the thesis; not committed
MIN_DAYS, MIN_LAUNCHES = 14, 100  # below this the charts and the tests of the thesis mean nothing
CHARTS = HERE / "charts"
NUMBERS = HERE / "data" / "numbers.md"
UTC = dt.timezone.utc
SUPPLY = 10 ** 9  # tokens per Nearly / JustHoot launch
LAUNCHPAD_SUFFIXES = (".nearlytrade.near", ".justhoot.near", ".launch.intear.near", ".launch-v2.intear.near")
MIN_TOKEN_NEAR = 0.1  # a wallet's entry into a token below this is noise (same threshold as the indexer's scoring)
ALIVE_NEAR, ALIVE_TRADERS = 10.0, 3  # "still has a market": volume and distinct traders over the last 3 days
ENTRY_BUCKETS = ((0, 10, "First 10 seconds"), (10, 60, "10 to 60 seconds"), (60, 600, "1 to 10 minutes"),
                 (600, 3600, "10 to 60 minutes"), (3600, 86400, "1 to 24 hours"), (86400, None, "After a day"))
ENTRY_PHASES = ((0, 600, "le_10m"), (600, 3600, "10m_1h"), (3600, None, "gt_1h"))


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


def misdated(db) -> set:
    """Launch records whose token traded before the record says it launched: a pool created against an older token, or
    a token of an earlier launchpad version. They are not launches of the window and are left out of every measure."""
    out = set()
    for d in db.dash_launches.find({"first_trade": {"$exists": True}}, {"t_first": 1, "first_trade": 1}):
        if (_aware(d["t_first"]) - _aware(d["first_trade"])).total_seconds() > 60:
            out.add(d["_id"])
    return out


def launches(db, near_usd: float, skip: set) -> dict:
    """Outcomes of launchpad launches: do they trade, how far do they run, are they alive, who creates them."""
    now = dt.datetime.now(UTC)
    decimals = {}
    docs = [d for d in db.dash_launches.find({}, {"pad": 1, "t_first": 1, "creator": 1, "px0": 1, "px": 1, "px_max": 1,
                                                  "trades": 1, "volume": 1, "last_trade": 1, "backfill": 1,
                                                  "fee_farm": 1, "extra.dev_buy": 1, "extra.tax": 1,
                                                  "extra.fee_pips": 1}) if d["_id"] not in skip]
    for m in db.ft_metadata.find({"_id": {"$in": [d["_id"] for d in docs]}}, {"decimals": 1}):
        decimals[m["_id"]] = m.get("decimals")
    recent: dict[str, list] = {}  # token -> [NEAR volume, traders] over the last 3 days
    for c in db.dash_candles.find({"i": "1h", "t": {"$gte": now - dt.timedelta(days=3)}}, {"token": 1, "v": 1, "w": 1}):
        r = recent.setdefault(c["token"], [0.0, set()])
        r[0] += float(c.get("v") or 0)
        r[1].update(c.get("w") or ())
    per_day: dict[str, Counter] = defaultdict(Counter)
    by_pad: Counter = Counter()
    creators: Counter = Counter()
    traded, x_max, x_now, vols, peak_caps, alive_7d, any_7d, old = 0, [], [], [], [], 0, 0, 0
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
                any_7d += 1  # a single dust sale counts here, so this number is not used as "alive"
            near, who = recent.get(d["_id"], (0.0, ()))
            if near >= ALIVE_NEAR and len(who) >= ALIVE_TRADERS:
                alive_7d += 1
    n = len(docs)
    top = creators.most_common(10)
    first = min((d["t_first"] for d in docs), default=None)
    return {
        "from": _day(first) if first else None, "to": _day(now), "total": n, "by_pad": dict(by_pad),
        "misdated_skipped": len(skip),
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
        "alive": {"older_than_7d": old, "traded_in_last_3d": alive_7d, "share": _share(alive_7d, old),
                  "min_near": ALIVE_NEAR, "min_traders": ALIVE_TRADERS,
                  "any_trade_in_last_3d": any_7d, "any_trade_share": _share(any_7d, old)},
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


def creators_behaviour(db, skip: set) -> dict:
    """What creators did with their own tokens (sniper_launch_stats): dumps within 5 minutes of trading."""
    docs = [d for d in db.sniper_launch_stats.find({}, {"creator": 1, "launchpad": 1, "dumped": 1, "dev_in": 1,
                                                        "dev_sold": 1, "first_sell_s": 1, "started_at": 1})
            if d["_id"] not in skip]
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


def _aware(t: dt.datetime) -> dt.datetime:
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _positions(db) -> list[tuple]:
    """Wallet x token positions of the flows window: (token, wallet, NEAR put in, result in NEAR, first buy).

    Result = the matched part of the position plus the rest at the last traded price - the formula the indexer ranks
    smart money with. Entries under MIN_TOKEN_NEAR are noise; creators in their own token are not in the flows."""
    prices = {d["_id"]: float(d.get("price") or 0) for d in db.sniper_smart_prices.find({}, {"price": 1})}
    out = []
    for f in db.sniper_smart_flows.find({}, {"wallet": 1, "token": 1, "near_in": 1, "near_out": 1, "tok_in": 1,
                                             "tok_out": 1, "first_at": 1}):
        near_in, near_out = float(f.get("near_in") or 0), float(f.get("near_out") or 0)
        tok_in, tok_out = float(f.get("tok_in") or 0), float(f.get("tok_out") or 0)
        if near_in < MIN_TOKEN_NEAR or tok_in <= 0 or not f.get("first_at"):
            continue
        m = min(tok_in, tok_out)
        realized = (near_out * m / tok_out if tok_out else 0.0) - near_in * m / tok_in
        left = tok_in - m
        held = left * prices.get(f["token"], 0.0) / 1e24 - near_in * left / tok_in
        out.append((f["token"], f["wallet"], near_in, realized + held, _aware(f["first_at"])))
    return out


def wallet_outcomes(positions: list[tuple]) -> dict:
    """Profit of trading wallets over the flows window, summed over each wallet's positions."""
    pnl: dict[str, float] = defaultdict(float)
    invested: dict[str, float] = defaultdict(float)
    tokens: Counter = Counter()
    first = min((p[4] for p in positions), default=None)
    last = max((p[4] for p in positions), default=None)
    for _, w, near_in, result, _ in positions:
        pnl[w] += result
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


def _pos_stats(rows: list[tuple]) -> dict:
    """Positions counted one by one, not weighted by size: one token took half of all NEAR put into launches."""
    roi = sorted(p[3] / p[2] for p in rows)
    n = len(roi)
    if not n:
        return {"n": 0}
    return {"n": n, "win_share": _share(sum(r > 0 for r in roi), n), "median_roi": round(roi[n // 2], 4),
            "mean_roi": round(sum(roi) / n, 4), "lost_half_share": _share(sum(r <= -0.5 for r in roi), n),
            "doubled_share": _share(sum(r >= 1 for r in roi), n)}


def edge(db, positions: list[tuple], skip: set) -> dict:
    """What separates the launches and positions that pay from the rest: who launched, when the buyer came, and the
    buyer's own record. Everything here is known at the launch or within its first minutes, except the outcomes."""
    launches = {d["_id"]: d for d in db.dash_launches.find({}, {"creator": 1, "t_first": 1, "first_trade": 1, "px0": 1,
                                                                 "px_max": 1, "volume": 1}) if d["_id"] not in skip}
    stats = {d["_id"]: d for d in db.sniper_launch_stats.find({}, {"dumped": 1, "dev_in": 1})}
    holders = {d["_id"]: int(d.get("holders") or 0) for d in db.dash_holder_stats.find({}, {"holders": 1})}
    pos = [p for p in positions if p[0] in launches]
    if not launches or not pos:
        return {"positions": 0}

    # the creator's record before each launch; a launchpad's own launcher account stands for many people - no record
    by_creator: dict[str, list] = defaultdict(list)
    for t, d in launches.items():
        if d.get("creator") and not d["creator"].endswith(LAUNCHPAD_SUFFIXES):
            by_creator[d["creator"]].append((_aware(d["t_first"]), t))
    prior, prior_dump, gap_h = {}, {}, {}
    for v in by_creator.values():
        v.sort()
        for i, (ts, t) in enumerate(v):
            prior[t] = i
            prior_dump[t] = sum(bool(stats.get(p, {}).get("dumped")) for _, p in v[:i])
            gap_h[t] = (ts - v[i - 1][0]).total_seconds() / 3600 if i else None

    def dev(t):
        return int(stats.get(t, {}).get("dev_in") or 0) > 0

    def dumped(t):
        return bool(stats.get(t, {}).get("dumped"))

    def x_max(t):
        d = launches[t]
        return d["px_max"] / d["px0"] if d.get("px0") and d.get("px_max") else None

    clean = {t for t in launches if prior.get(t) == 0 and dev(t) and not dumped(t)}
    groups = {
        "all": set(launches),
        "first": {t for t in launches if prior.get(t) == 0},
        "repeat": {t for t in launches if prior.get(t, 0) >= 1},
        "repeat3": {t for t in launches if prior.get(t, 0) >= 3},
        "dumped_before": {t for t in launches if prior_dump.get(t, 0) >= 1},
        "relaunch_3h": {t for t in launches if gap_h.get(t) is not None and gap_h[t] < 3},
        "dumped_now": {t for t in launches if dumped(t)},
        "no_dev_buy": {t for t in launches if t in prior and not dev(t)},
        "clean": clean,
        "rest": set(launches) - clean,
    }

    def token_stats(tokens) -> dict:
        xs = [x for x in (x_max(t) for t in tokens) if x is not None]
        n = len(tokens)
        return {"n": n, "traded": len(xs), "x2": sum(x >= 2 for x in xs), "x2_share": _share(sum(x >= 2 for x in xs), len(xs)),
                "x5_share": _share(sum(x >= 5 for x in xs), len(xs)),
                "hold10_share": _share(sum(holders.get(t, 0) >= 10 for t in tokens), n),
                "vol100_share": _share(sum(float(launches[t].get("volume") or 0) >= 100 for t in tokens), n)}

    times = sorted(_aware(d["t_first"]) for d in launches.values())
    mid = times[len(times) // 2] if times else None
    halves = {}
    for name, sel in (("first_half", {t for t, d in launches.items() if _aware(d["t_first"]) < mid}),
                      ("second_half", {t for t, d in launches.items() if _aware(d["t_first"]) >= mid})):
        halves[name] = {k: token_stats(groups[k] & sel) for k in ("clean", "rest", "first", "repeat")}

    def delay(p) -> float | None:
        ft = launches[p[0]].get("first_trade")
        return max((p[4] - _aware(ft)).total_seconds(), 0.0) if ft else None

    def within(p, a, b) -> bool:
        s = delay(p)
        return s is not None and s >= a and (b is None or s < b)

    entry = [{"label": label, "from_s": a, "to_s": b, **_pos_stats([p for p in pos if within(p, a, b)])}
             for a, b, label in ENTRY_BUCKETS]
    by_launch = {g: {key: _pos_stats([p for p in pos if (p[0] in clean) == (g == "clean") and within(p, a, b)])
                     for a, b, key in ENTRY_PHASES} for g in ("clean", "rest")}
    early_halves = {name: {g: _pos_stats([p for p in pos if (p[0] in clean) == (g == "clean") and within(p, 0, 600)
                                          and ((_aware(launches[p[0]]["t_first"]) < mid) == (name == "first_half"))])
                           for g in ("clean", "rest")} for name in ("first_half", "second_half")}

    # does a wallet's result carry over? wallets judged on entries before a date, measured on entries after it
    wal: dict[str, list] = defaultdict(list)
    for p in positions:
        wal[p[1]].append(p)
    t0, t1 = min(p[4] for p in positions), max(p[4] for p in positions)
    persistence = []
    for frac in (0.4, 0.5, 0.6):
        split = t0 + (t1 - t0) * frac
        g: dict[str, list] = {"profit_before": [], "loss_before": [], "new": []}
        for rows in wal.values():
            before, after = [p for p in rows if p[4] < split], [p for p in rows if p[4] >= split]
            if not after:
                continue
            if len(before) >= 3:
                g["profit_before" if sum(p[3] for p in before) > 0 else "loss_before"].append(after)
            elif not before:
                g["new"].append(after)
        persistence.append({"split": _day(split), **{k: {
            "wallets": len(v), "profit_after_share": _share(sum(sum(p[3] for p in a) > 0 for a in v), len(v)),
            "positions": _pos_stats([p for a in v for p in a])} for k, v in g.items()}})

    invested: Counter = Counter()
    for p in pos:
        invested[p[0]] += p[2]
    top = invested.most_common(1)
    big = top[0][0] if top else None
    by_volume = sorted(launches, key=lambda t: -float(launches[t].get("volume") or 0))[:10]
    x10 = {t for t in launches if (x_max(t) or 0) >= 10}
    x2 = {t for t in launches if (x_max(t) or 0) >= 2}
    return {
        "positions": len(pos), "wallets": len({p[1] for p in pos}), "tokens": len({p[0] for p in pos}),
        "split_day": _day(mid) if mid else None, "launcher_accounts_skipped": sum(
            1 for d in launches.values() if (d.get("creator") or "").endswith(LAUNCHPAD_SUFFIXES)),
        "launches": {k: token_stats(v) for k, v in groups.items()}, "halves": halves,
        "entry": entry, "entry_by_launch": by_launch, "early_by_half": early_halves, "persistence": persistence,
        "recall": {"x2": len(x2), "x2_in_clean": len(x2 & clean), "x10": len(x10), "x10_in_clean": len(x10 & clean),
                   "top10_volume_in_clean": sum(t in clean for t in by_volume),
                   "top10_volume_by_repeat": sum(prior.get(t, 0) >= 1 for t in by_volume)},
        "largest": {"token": big, "share_of_near": _share_f(top[0][1], sum(invested.values())),
                    "positions": sum(p[0] == big for p in pos), "creator_earlier_launches": prior.get(big),
                    "minutes_since_previous": _r(gap_h[big] * 60, 0) if gap_h.get(big) is not None else None, "x_max": _r(x_max(big), 0),
                    **{k: v for k, v in _pos_stats([p for p in pos if p[0] == big]).items() if k != "n"}} if big else {},
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


def holders(db, skip: set) -> dict:
    """Holder counts of launch tokens whose balances are exact (complete) or reconciled (seeded)."""
    counts = [int(d.get("holders") or 0) for d in db.dash_holder_stats.find(
        {"$or": [{"complete": True}, {"seeded": {"$exists": True}}]}, {"holders": 1}) if d["_id"] not in skip]
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
    positions = _positions(db)
    skip = misdated(db)
    return {
        "generated_at": dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "near_usd": near_usd,
        "dex_daily": dex_daily(db), "concentration": token_concentration(db),
        "launches": launches(db, near_usd, skip), "creators": creators_behaviour(db, skip),
        "edge": edge(db, positions, skip),
        "wallets": wallet_outcomes(positions), "network": network(db, near_usd), "holders": holders(db, skip),
        "moves": big_moves(db), "launch_speed": launch_speed(db), "near_price": near_price(db),
    }


def short_of(snap: dict) -> list[str]:
    """What a collected snapshot lacks for the thesis to be rebuilt from it; empty when it is enough."""
    today = snap["generated_at"][:10]
    days = sum(r["day"] < today for r in snap["dex_daily"])
    out = []
    if days < MIN_DAYS:
        out.append(f"{days} full days of DEX candles (needs {MIN_DAYS})")
    if snap["launches"]["total"] < MIN_LAUNCHES:
        out.append(f"{snap['launches']['total']} launches (needs {MIN_LAUNCHES})")
    if not snap["edge"].get("positions"):
        out.append("no wallet positions in launch tokens (sniper_smart_flows)")
    if not snap["near_price"]:
        out.append("no NEAR price history (ft_price_snapshots)")
    if not any(d["full"] for d in snap["network"]["days"]):
        out.append("no full day of network metrics (dash_network)")
    return out


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
        short = short_of(snap)
        if short:
            LOCAL.write_text(json.dumps(snap, indent=1), encoding="utf-8", newline="\n")
            print("This database is too short for the thesis: " + "; ".join(short) + ".")
            print(f"What was collected is in {LOCAL.relative_to(HERE.parent)}; the committed snapshot, numbers and "
                  f"charts are left as they were. Let the indexer run, or fill history with "
                  f"indexer/scripts/backfill_dash.py, and collect again.")
            return
        SNAPSHOT.write_text(json.dumps(snap, indent=1), encoding="utf-8", newline="\n")
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    from render import render  # charts and the numbers table

    render(snap, CHARTS, NUMBERS)
    print(f"snapshot of {snap['generated_at']}: numbers -> {NUMBERS.relative_to(HERE.parent)}, "
          f"charts -> {CHARTS.relative_to(HERE.parent)}")


if __name__ == "__main__":
    main()
