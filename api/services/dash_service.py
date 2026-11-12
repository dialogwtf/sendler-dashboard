"""Sendler Dash (/v2/dash) — reads the indexer's dash_* collections and the sniper's creator data.

Read-only, never calls RPC. Every endpoint result is cached in memory per query string (TTL per
endpoint, single-flight), so a crowd of visitors costs one Mongo query per TTL. Prices in the
collections are NEAR per raw token unit (px); the human price and market cap use ft_metadata decimals.
Data contract: indexer/docs/DASH_DATA.md.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import re
import time

from config import dash as cfg
from core.database import Database
from services.dash_kit import TTLCache

CACHE = TTLCache()
_META: dict[str, tuple[float, dict]] = {}
META_TTL_S = 600
# A token not in ft_metadata yet (or without decimals): asked again soon.
META_MISS_TTL_S = 30


class DashError(Exception):
    """Bad request parameter → 400 {"detail": code}."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _db():
    return Database.get_db("near_indexer")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def human_px(px: float | None, decimals: int | None) -> float | None:
    """NEAR per raw unit → NEAR per token."""
    if px is None or decimals is None:
        return None
    return px * 10 ** decimals


def bot_code(account: str) -> str:
    """Code of a bot link for a token or a wallet: t.me/<bot>?start=t_<code> (buy), wt_<code> / ww_<code> (watch).
    Same as token_code in indexer/services/dashboard/botlinks.py — change both together; the code → address map
    (sniper_token_links) is written by the dashboard worker: traded and launched tokens, creators, smart wallets."""
    return base64.b32encode(hashlib.sha256(account.encode()).digest()).decode().lower()[:10]


def label_of(account: str | None, labels: dict) -> dict | None:
    if not account:
        return None
    if account in labels:
        return labels[account]
    for suffix, (label, kind) in cfg.CONTRACT_SUFFIXES.items():
        if account.endswith(suffix):
            return {"label": label, "kind": kind}
    if len(account) == 64 and all(c in "0123456789abcdef" for c in account):
        return {"label": None, "kind": "implicit"}
    return None


def tax_pct(pad: str | None, extra: dict) -> float | None:
    """Launch tax in percent (the data contract with the site: a number or None — unknown, never 0 by guess).
    Nearly: `tax_opts` object {buy_bps, sell_bps, …} → the larger side; JustHoot: `fee_pips` — the token's tax on
    transfers from/to DCL (10000 pips = 1%); a number already — as is."""
    tax = extra.get("tax")
    if isinstance(tax, (int, float)) and not isinstance(tax, bool):
        return float(tax)
    try:
        if isinstance(tax, dict):
            return max(int(tax.get("buy_bps") or 0), int(tax.get("sell_bps") or 0)) / 100
        if pad == "justhoot" and extra.get("fee_pips") is not None:
            return int(extra["fee_pips"]) / 10_000
    except (TypeError, ValueError):
        return None
    return None


def _parse_iso(value: str, code: str) -> dt.datetime:
    try:
        t = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise DashError(code)
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


# ─── lookups ──────────────────────────────────────────────────────────────────

async def meta(tokens: list[str]) -> dict[str, dict]:
    """symbol / name / decimals from ft_metadata (no icons — they are big), cached 10 min.

    A token without decimals yet (a fresh launch: its row is asked for before the indexer writes
    ft_metadata) is cached only META_MISS_TTL_S — 10 min of «no decimals» meant 10 min with no price
    and no market cap on a launch, exactly when people look at it."""
    global _META
    now = time.monotonic()
    missing = [t for t in tokens if t not in _META or _META[t][0] < now]
    if missing:
        found = {}
        async for d in _db().ft_metadata.find({"_id": {"$in": missing}}, {"symbol": 1, "name": 1, "decimals": 1}):
            found[d["_id"]] = {"symbol": d.get("symbol"), "name": d.get("name"), "decimals": d.get("decimals")}
        for t in missing:
            m = found.get(t, {})
            _META[t] = (now + (META_TTL_S if m.get("decimals") is not None else META_MISS_TTL_S), m)
        if len(_META) > 50_000:
            _META = {k: v for k, v in _META.items() if v[0] > now}
    return {t: _META[t][1] for t in tokens}


async def near_usd() -> float | None:
    async def make():
        d = await _db().ft_prices.find_one({"_id": "wrap.near"}, {"price_usd": 1})
        return float(d["price_usd"]) if d and d.get("price_usd") else None
    return await CACHE.get("near_usd", 60, make)


async def label_map(accounts) -> dict[str, dict]:
    ids = sorted({a for a in accounts if a})
    if not ids:
        return {}
    out = {}
    async for d in _db().dash_labels.find({"_id": {"$in": ids}}, {"label": 1, "kind": 1}):
        label = d.get("label")
        out[d["_id"]] = {"label": None if label in cfg.LEGACY_TEXT_LABELS else label, "kind": d.get("kind")}
    return out


async def smart_ids() -> list[str]:
    async def make():
        cur = _db().sniper_smart_wallets.find({}, {"_id": 1}).sort("rank", 1).limit(200)
        return [d["_id"] async for d in cur]
    return await CACHE.get("smart_ids", 300, make)


SMART_MIN_IN_NEAR = 0.1  # = indexer/services/dashboard/smart.py MIN_TOKEN_NEAR: smaller entries are noise
SMART_DUST_SHARE = 0.02  # a wallet with less than 2% of what it bought left has exited


async def smart_position(tid: str, smart: list[str], px: float | None, dec: int | None) -> dict | None:
    """Where the top-200 smart wallets stand in a token, from their swap flows (sniper_smart_flows, swaps only, kept
    45 days after a wallet's last trade in it): how many bought, how many still hold, what share of the bought tokens
    is not sold, their volume-weighted entry price and the current price against it.

    `px` — the current price, NEAR per raw unit (last 1m candle). Tokens a wallet got not by a swap (transfers, tax
    payouts) are not in the flows, so a wallet that sold more than it bought counts as holding nothing."""
    if not smart:
        return None
    rows = [f async for f in _db().sniper_smart_flows.find(
        {"_id": {"$in": [f"{w}:{tid}" for w in smart]}}, {"near_in": 1, "tok_in": 1, "tok_out": 1})]
    near_in = tok_in = left = 0.0
    wallets = holding = 0
    for f in rows:
        n, t_in, t_out = float(f.get("near_in") or 0), float(f.get("tok_in") or 0), float(f.get("tok_out") or 0)
        if n < SMART_MIN_IN_NEAR or t_in <= 0:
            continue
        wallets += 1
        rest = max(0.0, t_in - t_out)
        holding += rest > t_in * SMART_DUST_SHARE
        near_in, tok_in, left = near_in + n, tok_in + t_in, left + rest
    if not wallets:
        return None
    entry = near_in / tok_in  # NEAR per raw unit
    return {"wallets": wallets, "holding": holding, "held": left / tok_in, "invested_near": near_in,
            "entry_near": human_px(entry, dec), "x": px / entry if px else None}


async def creator_facts(creators: list[str]) -> dict[str, dict]:
    """Launches, dumps, how many sniper users blocked the creator, linked wallets — per creator."""
    creators = sorted({c for c in creators if c})
    out = {c: {"launches": 0, "dumped": 0, "blocked_by": 0, "linked": False} for c in creators}
    if not creators:
        return out
    db = _db()
    async for d in db.sniper_launch_stats.aggregate([
            {"$match": {"creator": {"$in": creators}}},
            {"$group": {"_id": "$creator", "n": {"$sum": 1}, "dumped": {"$sum": {"$cond": ["$dumped", 1, 0]}}}}]):
        out[d["_id"]].update(launches=d["n"], dumped=d["dumped"])
    async for d in db.sniper_devs_blocked.aggregate([
            {"$match": {"account": {"$in": creators}, "allowed": {"$ne": True}}},
            {"$group": {"_id": "$account", "n": {"$sum": 1}}}]):
        out[d["_id"]]["blocked_by"] = d["n"]
    async for d in db.sniper_dev_links.find({"_id": {"$in": creators}}, {"_id": 1}):
        out[d["_id"]]["linked"] = True
    shared = await shared_blocked()
    for c in creators:
        out[c]["shared_block"] = c in shared  # in the trade bot's shared auto-block - hidden by the hide_blocked filter
    return out


async def shared_blocked() -> set[str]:
    """Creators in the sniper's shared auto-block: auto-blocked (not unblocked) by at least SHARED_BLOCK_MIN_USERS
    users, or one of them recorded a dev sale of at least SHARED_BLOCK_MIN_NEAR. Same rule as SharedAutoBlocks."""
    async def make():
        db = _db()
        docs = [d async for d in db.sniper_devs_blocked.find(
            {"source": "auto", "allowed": {"$ne": True}}, {"user": 1, "account": 1, "token": 1, "sold_near": 1})]
        missing = list({d["token"] for d in docs if d.get("sold_near") is None and d.get("token")})
        sold = {s["_id"]: float(s.get("dev_sold_near") or 0) async for s in db.sniper_launch_stats.find(
            {"_id": {"$in": missing}}, {"dev_sold_near": 1})} if missing else {}
        votes: dict[str, dict] = {}
        for d in docs:
            if d.get("account") and d.get("user") is not None:
                near = d.get("sold_near")
                votes.setdefault(d["account"], {})[d["user"]] = float(
                    near if near is not None else sold.get(d.get("token"), 0))
        return {a for a, v in votes.items()
                if len(v) >= cfg.SHARED_BLOCK_MIN_USERS or max(v.values()) >= cfg.SHARED_BLOCK_MIN_NEAR}
    return await CACHE.get("shared_blocked", 60, make)


async def launch_rows(docs: list[dict]) -> list[dict]:
    tokens = [d["_id"] for d in docs]
    m_all, usd = await meta(tokens), await near_usd()
    facts = await creator_facts([d.get("creator") for d in docs])
    rows = []
    for d in docs:
        m = m_all.get(d["_id"], {})
        dec = m.get("decimals")
        px, px0, extra = d.get("px"), d.get("px0"), d.get("extra") or {}
        cap_near = None
        if px is not None and dec is not None and d.get("pad") in cfg.SUPPLY_PADS:
            cap_near = px * cfg.LAUNCH_SUPPLY * 10 ** dec
        rows.append({
            "token": d["_id"], "pad": d.get("pad"), "symbol": d.get("symbol") or m.get("symbol"),
            "name": d.get("name") or m.get("name"), "creator": d.get("creator"),
            "creator_facts": facts.get(d.get("creator")), "stage": cfg.STAGE_NAMES.get(d.get("stage_n")),
            "started": d.get("t_started") or d.get("t_first"), "ready": d.get("t_ready"),
            "dev_buy": extra.get("dev_buy"), "tax": tax_pct(d.get("pad"), extra),
            # launch "on bot tax" (no dev buy, the tax goes to the creator): a flag; the numbers are dev_buy / tax
            "fee_farm": bool(d.get("fee_farm")), "pool": d.get("pool"),
            "price_near": human_px(px, dec), "x": (px / px0) if px and px0 else None,
            "x_max": (d.get("px_max") / px0) if d.get("px_max") and px0 else None,
            "cap_near": cap_near, "cap_usd": cap_near * usd if cap_near is not None and usd else None,
            "trades": d.get("trades", 0), "volume_near": d.get("volume", 0.0), "last_trade": d.get("last_trade"),
            "links": extra.get("links"),
            # loaded from history (indexer/scripts/backfill_dash.py): no dev buy / tax / fee_farm known
            "backfill": bool(d.get("backfill"))})
    return rows


# ─── endpoints ────────────────────────────────────────────────────────────────

async def health() -> dict:
    cur = await _db().dash_state.find_one({"_id": "cursor"}) or {}
    at = cur.get("at")
    if isinstance(at, dt.datetime) and at.tzinfo is None:
        at = at.replace(tzinfo=dt.timezone.utc)
    lag = (_now() - at).total_seconds() if isinstance(at, dt.datetime) else None
    return {"height": cur.get("height"), "behind_s": lag, "near_usd": await near_usd()}


async def launches(pads: list[str], hide_fee_farm: bool, before: str | None, limit: int,
                   hide_blocked: bool = False) -> list[dict]:
    flt: dict = {}
    if pads:
        flt["pad"] = {"$in": pads}
    if hide_fee_farm:
        flt["fee_farm"] = {"$in": [None, ""]}
    if hide_blocked:
        flt["creator"] = {"$nin": sorted(await shared_blocked())}
    if before:
        flt["t_first"] = {"$lt": _parse_iso(before, "bad_before")}
    docs = [d async for d in _db().dash_launches.find(flt).sort("t_first", -1).limit(limit)]
    return await launch_rows(docs)


async def movers(period: str, limit: int) -> list[dict]:
    if period not in ("10m", "1h", "24h"):
        raise DashError("bad_period")
    db = _db()
    since = _now() - dt.timedelta(seconds=cfg.PERIODS[period])
    docs = [d async for d in db.dash_launches.find({"last_trade": {"$gte": since}}).limit(500)]
    if not docs:
        return []
    interval = "1h" if period == "24h" else "1m"
    win = {}
    async for c in db.dash_candles.aggregate([
            {"$match": {"token": {"$in": [d["_id"] for d in docs]}, "i": interval,
                        "t": {"$gte": since.replace(second=0, microsecond=0)}}},
            {"$sort": {"t": 1}},
            {"$group": {"_id": "$token", "o": {"$first": "$o"}, "c": {"$last": "$c"}, "v": {"$sum": "$v"},
                        "v_usd": {"$sum": "$v_usd"}, "buys": {"$sum": "$buys"}, "sells": {"$sum": "$sells"},
                        "spark": {"$push": "$c"}}}]):
        win[c["_id"]] = c
    m = await meta([d["_id"] for d in docs])
    rows = []
    for row in await launch_rows(docs):
        w = win.get(row["token"])
        if not w or not w.get("o") or (w.get("v_usd") or 0) < cfg.MOVERS_MIN_USD:
            continue
        dec = m.get(row["token"], {}).get("decimals")
        row.update(x_period=w["c"] / w["o"], volume_period_near=w["v"], volume_period_usd=w.get("v_usd"),
                   buys_period=w["buys"], sells_period=w["sells"],
                   spark=[human_px(x, dec) for x in w.get("spark") or []])  # closes of the period's candles, NEAR per token
        rows.append(row)
    rows.sort(key=lambda r: r["x_period"], reverse=True)
    return rows[:limit]


async def tokens_top(period: str, sort: str, limit: int) -> list[dict]:
    if period not in ("1h", "24h", "7d") or sort not in ("volume", "growth", "trades"):
        raise DashError("bad_period_or_sort")
    interval = "1m" if period == "1h" else "1h"
    since = (_now() - dt.timedelta(seconds=cfg.PERIODS[period])).replace(second=0, microsecond=0)
    if interval == "1h":
        since = since.replace(minute=0)
    rows = [r async for r in _db().dash_candles.aggregate([
        {"$match": {"i": interval, "t": {"$gte": since}}},
        {"$sort": {"t": 1}},
        {"$group": {"_id": "$token", "o": {"$first": "$o"}, "c": {"$last": "$c"}, "v": {"$sum": "$v"},
                    "v_usd": {"$sum": "$v_usd"}, "trades": {"$sum": {"$add": ["$buys", "$sells"]}}}},
        {"$match": {"v": {"$gte": 1}}}], allowDiskUse=True)]
    for r in rows:
        r["growth"] = r["c"] / r["o"] if r.get("o") else None
    key = {"volume": lambda r: r["v"], "trades": lambda r: r["trades"], "growth": lambda r: r["growth"] or 0}[sort]
    rows.sort(key=key, reverse=True)
    rows = rows[:limit]
    ids = [r["_id"] for r in rows]
    m = await meta(ids)
    wallets: dict[str, set] = {t: set() for t in ids}  # unique traders over the period - the union of the candles
    async for c in _db().dash_candles.find({"token": {"$in": ids}, "i": interval, "t": {"$gte": since}},
                                           {"token": 1, "w": 1}):
        wallets[c["token"]].update(c.get("w") or ())
    return [{"token": r["_id"], "symbol": m[r["_id"]].get("symbol"), "decimals": m[r["_id"]].get("decimals"),
             "price_near": human_px(r["c"], m[r["_id"]].get("decimals")), "growth": r["growth"],
             "volume_near": r["v"], "volume_usd": r.get("v_usd"), "trades": r["trades"],
             "traders": len(wallets[r["_id"]])} for r in rows]


async def token(tid: str) -> dict:
    db = _db()
    md = await db.ft_metadata.find_one({"_id": tid}, {"symbol": 1, "name": 1, "decimals": 1, "icon": 1}) or {}
    dec = md.get("decimals")
    since = _now() - dt.timedelta(days=1)
    day = [c async for c in db.dash_candles.find(
        {"token": tid, "i": "1h", "t": {"$gte": since.replace(minute=0, second=0, microsecond=0)}}).sort("t", 1)]
    last = await db.dash_candles.find_one({"token": tid, "i": "1m"}, sort=[("t", -1)]) or {}
    launch = await db.dash_launches.find_one({"_id": tid})
    smart = await smart_ids()
    sm = {"buy": 0.0, "sell": 0.0, "wallets": 0}
    if smart:
        wallets = set()
        async for r in db.dash_trades.aggregate([
                {"$match": {"token": tid, "wallet": {"$in": smart}, "ts": {"$gte": since}}},
                {"$group": {"_id": {"w": "$wallet", "s": "$side"}, "near": {"$sum": "$near"}}}]):
            sm[r["_id"]["s"]] += r["near"]
            wallets.add(r["_id"]["w"])
        sm["wallets"] = len(wallets)
    pos = await smart_position(tid, smart, last.get("c"), dec)
    usd = await near_usd()
    price = human_px(last.get("c"), dec)
    return {"token": tid, "bot_code": bot_code(tid), "symbol": md.get("symbol"), "name": md.get("name"),
            "decimals": dec, "icon": md.get("icon"),
            "price_near": price, "price_usd": price * usd if price is not None and usd else None,
            "last_trade": last.get("updated_at"),
            "change_24h": (day[-1]["c"] / day[0]["o"] - 1) if day and day[0].get("o") else None,
            "volume_24h_near": sum(c.get("v", 0) for c in day),
            "volume_24h_usd": sum(c.get("v_usd", 0) for c in day),
            "trades_24h": sum(c.get("buys", 0) + c.get("sells", 0) for c in day),
            "traders_24h": len(set().union(*(c.get("w") or () for c in day))),
            "launch": (await launch_rows([launch]))[0] if launch else None, "smart_24h": sm, "smart_pos": pos}


async def candles(tid: str, interval: str, start: float | None, end: float | None, limit: int) -> dict:
    if interval not in ("1m", "1h", "1d"):
        raise DashError("bad_interval")
    flt: dict = {"token": tid, "i": interval}
    rng = {}
    for value, op in ((start, "$gte"), (end, "$lt")):
        if value is not None:
            rng[op] = dt.datetime.fromtimestamp(value, dt.timezone.utc)
    if rng:
        flt["t"] = rng
    proj = {"_id": 0, "t": 1, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1, "v_usd": 1, "buys": 1, "sells": 1, "traders": 1}
    docs = [d async for d in _db().dash_candles.find(flt, proj).sort("t", -1).limit(limit)]
    docs.reverse()
    return {"decimals": (await meta([tid]))[tid].get("decimals"), "candles": docs}


def _before(flt: dict, field: str, before: str | None) -> dict:
    """Older page: `before` = ISO time of the last row the client has."""
    if before:
        flt[field] = {"$lt": _parse_iso(before, "bad_before")}
    return flt


async def trades(tid: str, limit: int, before: str | None = None) -> list[dict]:
    db = _db()
    docs = [d async for d in db.dash_trades.find(_before({"token": tid}, "ts", before),
                                                 {"expires_at": 0}).sort("ts", -1).limit(limit)]
    dec = (await meta([tid]))[tid].get("decimals")
    smart = set(await smart_ids())
    labels = await label_map(d.get("wallet") for d in docs)
    launch = await db.dash_launches.find_one({"_id": tid}, {"creator": 1})
    creator = (launch or {}).get("creator")
    for d in docs:
        w = d.get("wallet")
        d["tags"] = (["smart"] if w in smart else []) + \
            (["creator"] if creator and creator in (w, d.get("signer")) else [])
        d["wallet_label"] = label_of(w, labels)
        d["decimals"] = dec
    return docs


async def creator(acc: str) -> dict:
    db = _db()
    stats = [d async for d in db.sniper_launch_stats.find({"creator": acc}).sort("t0", -1).limit(50)]
    recent = [d async for d in db.dash_launches.find({"creator": acc}).sort("t_first", -1).limit(10)]
    links = await db.sniper_dev_links.find_one({"_id": acc}, {"links": 1, "hop": 1}) or {}
    blocked_flt = {"account": acc, "allowed": {"$ne": True}}
    blocked = [d async for d in db.sniper_devs_blocked.find(
        blocked_flt, {"reason": 1, "source": 1, "added_at": 1, "token": 1}).sort("added_at", -1).limit(5)]
    blocked_by = await db.sniper_devs_blocked.count_documents(blocked_flt)

    def sold_share(s):
        try:
            return int(s["dev_sold"]) / int(s["dev_in"]) if int(s.get("dev_in") or 0) else None
        except (KeyError, TypeError, ValueError):
            return None

    def iso(ts):  # sniper_launch_stats keeps unix seconds; the API speaks ISO everywhere
        try:
            return dt.datetime.fromtimestamp(float(ts), dt.timezone.utc) if ts else None
        except (TypeError, ValueError, OverflowError):
            return None

    past = [{"token": s.get("token") or s["_id"], "pad": s.get("launchpad"), "started": iso(s.get("t0")),
             "dumped": s.get("dumped"), "first_sell_s": s.get("first_sell_s"), "sold_share": sold_share(s),
             "sold_near": s.get("dev_sold_near")} for s in stats]
    return {"account": acc, "bot_code": bot_code(acc), "launches": len(past), "dumped": sum(1 for p in past if p["dumped"]),
            "history": past, "recent": await launch_rows(recent),
            "links": [{**x, "at": iso(x.get("at"))} for x in links.get("links", [])[:20]], "blocked_by": blocked_by,
            "blocked_reasons": [{"reason": b.get("reason"), "source": b.get("source"), "token": b.get("token"),
                                 "at": b.get("added_at")} for b in blocked]}


async def smart_top(limit: int) -> list[dict]:
    return [d async for d in _db().sniper_smart_wallets.find({}).sort("rank", 1).limit(limit)]


async def smart_feed(limit: int, before: str | None = None) -> list[dict]:
    ids = await smart_ids()
    if not ids:
        return []
    db = _db()
    flt = _before({"wallet": {"$in": ids}}, "ts", before)
    docs = [d async for d in db.dash_trades.find(flt, {"expires_at": 0}).sort("ts", -1).limit(limit)]
    ranks = {d["_id"]: d async for d in db.sniper_smart_wallets.find(
        {"_id": {"$in": list({d["wallet"] for d in docs})}}, {"rank": 1, "roi": 1, "pnl": 1})}
    m = await meta(list({d["token"] for d in docs}))
    for d in docs:
        d["smart"] = ranks.get(d["wallet"])
        d["symbol"] = m[d["token"]].get("symbol")
        d["decimals"] = m[d["token"]].get("decimals")
    return docs


async def moves(min_usd: float, tok: str | None, kind: str | None, limit: int,
                before: str | None = None) -> list[dict]:
    flt = _before({"usd": {"$gte": min_usd}}, "ts", before)
    if tok:
        flt["token"] = tok
    if kind in ("transfer", "swap"):
        flt["kind"] = kind
    docs = [d async for d in _db().dash_moves.find(flt, {"expires_at": 0}).sort("ts", -1).limit(limit)]
    labels = await label_map([a for d in docs for a in (d.get("from"), d.get("to"))])
    m = await meta(list({d["token"] for d in docs if d.get("token") != "near"}))
    for d in docs:
        d["from_label"], d["to_label"] = label_of(d.get("from"), labels), label_of(d.get("to"), labels)
        d["symbol"] = "NEAR" if d.get("token") == "near" else m.get(d["token"], {}).get("symbol")
        d["decimals"] = 24 if d.get("token") == "near" else m.get(d["token"], {}).get("decimals")
    return docs


async def network(period: str) -> dict:
    if period not in ("1h", "24h", "7d"):
        raise DashError("bad_period")
    db = _db()
    interval = "1m" if period == "1h" else "1h"
    since = _now() - dt.timedelta(seconds=cfg.PERIODS[period])
    series = [d async for d in db.dash_network.find(
        {"i": interval, "t": {"$gte": since}}, {"_id": 0, "hll": 0, "expires_at": 0}).sort("t", 1)]
    day = await db.dash_network.find_one({"i": "1d"}, {"_id": 0, "hll": 0}, sort=[("t", -1)])
    cur = series[-2] if len(series) >= 2 else (series[-1] if series else None)  # last complete bucket
    tps = cur["txs"] / (60 if interval == "1m" else 3600) if cur else None
    return {"interval": interval, "series": series, "today": day, "tps": tps, "near_usd": await near_usd()}


async def new(kind: str | None, limit: int, before: str | None = None) -> list[dict]:
    flt = _before({}, "first_seen", before)
    if kind in ("pool", "contract"):
        flt["kind"] = kind
    elif kind in ("ft", "nft"):
        flt["token_kind"] = kind
    return [d async for d in _db().dash_new.find(flt, {"expires_at": 0}).sort("first_seen", -1).limit(limit)]


async def labels(accounts: list[str]) -> dict:
    found = await label_map(accounts)
    return {a: label_of(a, found) for a in accounts}


async def tokens_meta(ids: list[str]) -> dict:
    """Metadata for list rows: symbol, name, decimals, icon (data: URI or URL — render only via <img>), bot link
    code. Unknown token — null."""
    ids = sorted({i for i in ids if i})
    found = {d["_id"]: d async for d in _db().ft_metadata.find(
        {"_id": {"$in": ids}}, {"symbol": 1, "name": 1, "decimals": 1, "icon": 1})}
    return {i: ({"symbol": found[i].get("symbol"), "name": found[i].get("name"), "decimals": found[i].get("decimals"),
                 "icon": found[i].get("icon"), "bot_code": bot_code(i)} if i in found else None) for i in ids}


ACCOUNT_RE = re.compile(r"^(([a-z\d]+[-_])*[a-z\d]+\.)*([a-z\d]+[-_])*[a-z\d]+$")


async def search(q: str, limit: int = 10) -> dict:
    """Tokens by symbol (exact match first, then prefix; with 24h volume higher) or by contract address; an account
    when the query looks like a NEAR account (label, launches as creator, smart money rank)."""
    q = (q or "").strip()
    if len(q) < 2 or len(q) > 64:
        return {"tokens": [], "accounts": []}
    db = _db()
    low = q.lower()
    rx = {"$regex": "(?i)^" + re.escape(q)}
    cand: dict[str, dict] = {}
    async for d in db.ft_metadata.find({"$or": [{"symbol": rx}, {"_id": low}]},
                                       {"symbol": 1, "name": 1, "decimals": 1}).limit(200):
        cand[d["_id"]] = d
    async for d in db.dash_launches.find({"$or": [{"symbol": rx}, {"_id": low}]}, {"symbol": 1, "name": 1}).limit(100):
        cand.setdefault(d["_id"], d)
    vol: dict[str, float] = {}
    if cand:
        day = _now().replace(hour=0, minute=0, second=0, microsecond=0)
        async for c in db.dash_candles.find({"token": {"$in": list(cand)}, "i": "1d", "t": {"$gte": day}},
                                            {"token": 1, "v": 1}):
            vol[c["token"]] = c.get("v") or 0.0

    def rank(t: str) -> tuple:
        sym = (cand[t].get("symbol") or "").lower()
        return (t != low, sym != low, -vol.get(t, 0.0), len(sym), t)

    tokens = [{"token": t, "symbol": cand[t].get("symbol"), "name": cand[t].get("name"),
               "decimals": cand[t].get("decimals"), "volume_24h_near": vol.get(t, 0.0), "bot_code": bot_code(t)}
              for t in sorted(cand, key=rank)[:limit]]
    accounts = []
    if ACCOUNT_RE.match(low) and ("." in low or len(low) == 64):
        found = await label_map([low])
        smart = await db.sniper_smart_wallets.find_one({"_id": low}, {"rank": 1, "roi": 1, "pnl": 1})
        accounts.append({"account": low, "label": label_of(low, found),
                         "launches": await db.sniper_launch_stats.count_documents({"creator": low}),
                         "smart": {k: smart.get(k) for k in ("rank", "roi", "pnl")} if smart else None,
                         "bot_code": bot_code(low)})
    return {"tokens": tokens, "accounts": accounts}


async def quotes(ids: list[str]) -> dict:
    """Watchlist rows for up to 100 tokens in two queries: price (close of the last hourly candle — it moves with every
    trade), change and volume over 24 h from hourly candles, cap for Nearly / JustHoot launches. Unknown — null."""
    ids = sorted({i for i in ids if i and ACCOUNT_RE.match(i)})[:cfg.WATCHLIST_MAX]
    if not ids:
        return {}
    db = _db()
    since = (_now() - dt.timedelta(hours=24)).replace(minute=0, second=0, microsecond=0)
    agg = {c["_id"]: c async for c in db.dash_candles.aggregate([
        {"$match": {"token": {"$in": ids}, "i": "1h", "t": {"$gte": since}}},
        {"$sort": {"t": 1}},
        {"$group": {"_id": "$token", "o": {"$first": "$o"}, "c": {"$last": "$c"}, "v": {"$sum": "$v"},
                    "at": {"$last": "$updated_at"}}}])}
    missing = [i for i in ids if i not in agg]
    for t in missing:  # no trades in 24 h - the last price from the last hourly candle
        last = await db.dash_candles.find_one({"token": t, "i": "1h"}, {"c": 1, "updated_at": 1}, sort=[("t", -1)])
        if last:
            agg[t] = {"o": None, "c": last.get("c"), "v": 0.0, "at": last.get("updated_at")}
    pads = {d["_id"]: d.get("pad") async for d in db.dash_launches.find({"_id": {"$in": ids}}, {"pad": 1})}
    m, usd = await meta(ids), await near_usd()
    out = {}
    for t in ids:
        a, dec = agg.get(t) or {}, m[t].get("decimals")
        price = human_px(a.get("c"), dec)
        cap = price * cfg.LAUNCH_SUPPLY if price is not None and pads.get(t) in cfg.SUPPLY_PADS else None
        out[t] = {"symbol": m[t].get("symbol"), "decimals": dec, "price_near": price,
                  "price_usd": price * usd if price is not None and usd else None,
                  "change_24h": (a["c"] / a["o"] - 1) if a.get("o") and a.get("c") else None,
                  "volume_24h_near": a.get("v"), "cap_near": cap, "cap_usd": cap * usd if cap is not None and usd else None,
                  "last_trade": a.get("at"), "pad": pads.get(t), "bot_code": bot_code(t)}
    return out
