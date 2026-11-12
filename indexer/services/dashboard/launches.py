"""Nearly / JustHoot / Intear launches for the dashboard: its own instance of the launch feed parser (feed.BlockParser
keeps state between blocks - one per stream) -> dash_launches.

Stages only move forward: started -> created -> funding -> live -> ready; the document keeps the time of each
(`t_<stage>`) and the highest one (`stage_n`, `$max`). The first trade of a launch token sets the start price
(`px0`), every next one - the last price (`px`, `last_trade`): the API computes the cap and the growth without candles.

`extra` holds plain BSON types only (`_plain`): Nearly links come from the feed as a `Links` object - `links` is
written as a dict "kind -> URL" and `description` separately; an object in $set used to fail the worker's whole
batch (9-10 Nov: not a single Nearly launch, network, new items and the window's events were lost). Pair assets of
launches (`quotes`: PRL, QTC, TEAR, meme assets) go to the worker: a swap of a token against them is a trade priced in NEAR through the asset (trades.trade_of `via`).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import logging

from pymongo import UpdateOne

from services.chain.swaps import NEAR, asset
from .fee_farm import fee_farm
from .feed import BlockParser, LaunchEvent

logger = logging.getLogger("dashboard")

STAGES = {"started": 1, "created": 2, "funding": 3, "live": 4, "ready": 5}
STAGE_NAMES = {v: k for k, v in STAGES.items()}
EXTRA_KEYS = ("dev_buy", "tax", "shares", "fee_pips", "links", "fallback", "launcher")
KNOWN_MAX = 20_000
INT64 = 2 ** 63
# pair assets without a launch in the feed: TEAR is the pair asset of Intear launch-v2 launches
SEED_QUOTES = frozenset({"token.intear.near"})


def _plain(v):
    """A value for BSON: dicts, lists, strings, numbers; a big integer - as a string, a dataclass - as a dict, anything
    else - as a string (a string in the document is better than a failed batch)."""
    if v is None or isinstance(v, (str, bool, float)):
        return v
    if isinstance(v, int):
        return v if -INT64 <= v < INT64 else str(v)
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_plain(x) for x in v]
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return _plain(dataclasses.asdict(v))
    return str(v)


def extra_fields(raw: dict | None) -> dict:
    """An event's extra -> document fields: EXTRA_KEYS as plain types; links - {kind: URL} and description separately."""
    out = {}
    for k in EXTRA_KEYS:
        if k not in (raw or {}):
            continue
        v = raw[k]
        if k == "links" and hasattr(v, "urls"):  # links.Links
            if v.urls:
                out["links"] = dict(v.urls)
            if getattr(v, "description", None):
                out["description"] = v.description
            continue
        out[k] = _plain(v)
    return out


def pool_quote(token: str, pool: str | None) -> str | None:
    """The launch's pair asset from the DCL pool key "x|y|fee": neither the token itself nor NEAR; otherwise None."""
    parts = (pool or "").split("|")
    if len(parts) != 3:
        return None
    other = [p for p in parts[:2] if p != token]
    q = asset(other[0]) if len(other) == 1 else None
    return q if q and q != NEAR else None


def _fee_farm(ev: LaunchEvent) -> str | None:
    try:
        return fee_farm(ev)
    except Exception:  # noqa: BLE001 - the flag is not critical for the feed
        return None


class LaunchTracker:
    def __init__(self) -> None:
        self.parser = BlockParser()
        self._ops: list[UpdateOne] = []
        self._events: list[dict] = []
        self._trades: dict[str, list] = {}  # token -> [px, when, px_max, trades, NEAR volume] until the flush
        self.tokens: dict[str, bool] = {}  # launch token -> px0 already written
        self.quotes: set[str] = set(SEED_QUOTES)  # pair assets of launches (not NEAR)
        self.creators: dict[str, str] = {}  # launch token -> creator (smart money skips him in his own token)
        self.fresh: list[str] = []  # tokens first seen since the last parse (the worker tracks their holders)

    def parse(self, block: dict) -> None:
        try:
            evs = self.parser.parse(block)
        except Exception:  # noqa: BLE001 - launches must not bring the worker down
            logger.exception("Sendler Dash: launch parsing failed")
            return
        for ev in evs:
            self.add(ev)

    def add(self, ev: LaunchEvent) -> None:
        if not ev.token or ev.stage not in STAGES:
            return  # Intear does not know the token before ready - wait for ready
        at = dt.datetime.fromtimestamp(ev.block_ts, dt.timezone.utc) if ev.block_ts else dt.datetime.now(dt.timezone.utc)
        n = STAGES[ev.stage]
        extra = extra_fields(ev.extra)
        q = pool_quote(ev.token, ev.pool)
        if q:
            self.quotes.add(q)
        if ev.creator:
            if len(self.creators) > 2 * KNOWN_MAX:
                self.creators.clear()
            self.creators[ev.token] = ev.creator
        on_insert = {"pad": ev.launchpad, "token": ev.token, "t_first": at}
        fields = {f"t_{ev.stage}": at, "updated_at": at}
        for k, v in (("creator", ev.creator), ("symbol", ev.symbol), ("name", ev.name), ("pool", ev.pool)):
            if v:
                fields[k] = v
        if extra:
            fields.update({f"extra.{k}": v for k, v in extra.items()})
        if ev.stage == "started":
            fields["block"] = ev.block
            fields["tx"] = ev.tx_hash
            farm = _fee_farm(ev)
            fields["fee_farm"] = farm
        self._ops.append(UpdateOne({"_id": ev.token}, {"$setOnInsert": on_insert, "$set": fields,
                                                       "$max": {"stage_n": n}}, upsert=True))
        self._events.append({"token": ev.token, "pad": ev.launchpad, "stage": ev.stage, "creator": ev.creator,
                             "symbol": ev.symbol, "at": at})
        if ev.token not in self.tokens:
            self.tokens[ev.token] = False
            self.fresh.append(ev.token)
            if len(self.tokens) > KNOWN_MAX:
                for t in list(self.tokens)[: KNOWN_MAX // 4]:
                    self.tokens.pop(t, None)

    def load(self, docs) -> None:
        """Recent launches from the database after a restart: their trades keep updating the price. A launch from the
        history without px0 (the live stream missed it) gets no start price: the first trade after a restart is not the first trade."""
        for d in docs:
            self.tokens[d["_id"]] = d.get("px0") is not None or bool(d.get("backfill"))
            if d.get("creator"):
                self.creators[d["_id"]] = d["creator"]

    def creator_of(self, token: str) -> str | None:
        return self.creators.get(token)

    def load_quotes(self, docs) -> None:
        """Pair assets of all launches from the database ({_id, pool})."""
        for d in docs:
            q = pool_quote(d.get("_id") or "", d.get("pool"))
            if q:
                self.quotes.add(q)

    def note_trade(self, token: str, px: float, ts: float, near: float, price: bool = True) -> None:
        """A trade of a launch token: the first sets the start price, each one - the last price and the volume (netted until
        the flush). price=False - a trade outside the main pool (trades.MainPools): volume and trade count only."""
        if token not in self.tokens:
            return
        at = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
        if price and not self.tokens[token]:
            self.tokens[token] = True
            self._ops.append(UpdateOne({"_id": token, "px0": {"$exists": False}},
                                       {"$set": {"px0": px, "first_trade": at}}))
        agg = self._trades.get(token)
        if agg is None:
            agg = self._trades[token] = [None, at, None, 0, 0.0]
        if price:
            agg[0], agg[2] = px, px if agg[2] is None else max(agg[2], px)
        agg[1], agg[3], agg[4] = at, agg[3] + 1, agg[4] + near

    def drain(self) -> tuple[list[UpdateOne], list[dict]]:
        ops, evs = self._ops, self._events
        for token, (px, at, px_max, n, near) in self._trades.items():
            upd: dict = {"$set": {"last_trade": at}, "$inc": {"trades": n, "volume": near}}
            if px is not None:
                upd["$set"]["px"] = px
                upd["$max"] = {"px_max": px_max}
            ops.append(UpdateOne({"_id": token}, upd))
        self._ops, self._events, self._trades = [], [], {}
        return ops, evs
