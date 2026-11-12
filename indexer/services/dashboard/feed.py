"""Launch feed: optimistic neardata blocks -> launchpad events (Nearly, JustHoot, Intear launch-v2).

Stages: started - the first signal (token and creator are known, no trading yet); created - the token's account is
created; live - liquidity is added to the launch pool in Rhea DCL (event dcl.ref liquidity_added for the pool from
started), trading is possible; funding (one block before live) - the launchpad moves the tokens into the pool
(ft_transfer_call of the token to the DCL from a launchpad account: lock_N.nearlytrade.near for Nearly,
v2-pool-creator for JustHoot v2); ready - the launch is complete, the pool trades (Nearly - event launch, JustHoot -
the first dcl_positions_recorded, Intear - pool_updated with LaunchV2 and the dev buy in the same receipt).

Launchpads are recognised by their root account, not by the exact contract address: on 2026-10-17 JustHoot moved to
v2-launchpad.justhoot.near (events hoot_launcher_v2, launch_requested no longer has token_id - it comes in
launch_priced/launch_planned of the next receipt, tokens are *.v2-launchpad.justhoot.near), and the feed missed them.
Now JustHoot is any account *.justhoot.near with hoot_launcher* events, Nearly - nearlytrade.near and its
sub-accounts with nearpad* events (launch_started / launch). The fallback path is
create_pool in the DCL: a launchpad account creates a pool for its sub-account token and there was no started for it
- the launchpad's format changed; the launch still gets into the feed (started with extra.fallback, ready with live).

Blocks come from the process's stream (indexer/block_stream.py, one per process). The parser itself never calls
neardata: BlockParser takes a block and returns events (services/dashboard/launches.py).
"""
from __future__ import annotations

import dataclasses
import logging
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field

from services.chain.block import (
    DCL,
    INTEAR_ENGINE,
    NEARLY,
    calls,
    events,
    header,
    items,
    outcomes,
    pool_key,
    signer,
)
from services.chain.intear import pool_updates

from .links import from_intear, from_nearly

logger = logging.getLogger("dashboard")

JUSTHOOT_ROOT = "justhoot.near"
JUSTHOOT = "launchpad.justhoot.near"  # the first launcher version; newer ones are other *.justhoot.near accounts
INTEAR_LAUNCHER = "launch-v2.intear.near"
LAUNCH_FEE = 10000  # Nearly and JustHoot pools in the DCL are always 1%
TX_MEMORY_S = 180.0
# started is emitted once per token: JustHoot v2 sends hoot_launcher* events with token_id later too (settlements,
# claims) - the memory of emitted tokens outlives the launch window, otherwise a second started would come in 3 min
EMITTED_MAX = 20_000
# JustHoot launcher events that mean "a launch is in progress". Other hoot_launcher* events with token_id are about
# tokens already launched (transfer_fee_claimed, platform_fee_received with token_id wrap.near…): after a restart
# the _emitted memory is empty, and they used to give a false started for an old token and for wrap.near
HOOT_LAUNCH_EVENTS = frozenset({"launch_requested", "launch_priced", "launch_planned", "meme_launched"})
LAUNCH_ROOTS = {"nearly": NEARLY, "justhoot": JUSTHOOT_ROOT}  # launchpad -> root account (authenticity is by address)
POINT_KEYS = ("init_point", "left_point", "right_point")


def launchpad_of_account(account: str | None) -> str | None:
    """The launchpad an account belongs to (the root itself or its sub-account), or None."""
    for lp, root in LAUNCH_ROOTS.items():
        if account and (account == root or account.endswith("." + root)):
            return lp
    return None


@dataclass
class LaunchEvent:
    launchpad: str  # nearly | justhoot | intear
    stage: str  # started | ready
    token: str | None
    creator: str | None
    symbol: str | None = None
    name: str | None = None
    pool: str | None = None
    block: int = 0
    tx_hash: str | None = None
    extra: dict = field(default_factory=dict)
    block_ts: float = 0.0  # block time (unix, s)
    seen: float = 0.0  # when the feed parsed the block (unix, s)

    @property
    def key(self) -> str:
        return f"{self.launchpad}:{self.token or self.tx_hash}"


def _intear_launch_pool(ev: dict) -> tuple[str, str | None, str] | None:
    """(pool, quote asset, launched asset) from a launch pool event: dex_events_borsh, or JSON before 2026-11-01."""
    if ev.get("event") == "dex_events_borsh":
        try:
            pools = pool_updates(ev.get("data"))
        except (ValueError, TypeError):
            return None
        return next(((str(p.pool_id), *p.assets) for p in pools if p.kind == "launch_v2"), None)
    inner = (ev.get("data") or {}).get("event") if ev.get("event") == "dex_event" else None
    if not isinstance(inner, dict) or inner.get("event") != "pool_updated":
        return None
    data = inner.get("data") or {}
    pool = (data.get("pool") or {}).get("LaunchV2")
    if not pool:
        return None
    return (str(data.get("pool_id")), (pool.get("quote_asset") or {}).get("asset_id"),
            (pool.get("launched_asset") or {}).get("asset_id") or "")


class BlockParser:
    """State between blocks: creator and symbol of an Intear launch are known from the launch_token transaction,
    the pool - from the engine's receipt of the same transaction a few blocks later; for JustHoot v2 the launch token
    comes in the receipt after launch_requested."""

    def __init__(self) -> None:
        self._intear_tx: dict[str, tuple[float, str, dict]] = {}  # tx_hash -> (when, creator, args)
        self._hoot_req: dict[str, tuple[float, dict]] = {}  # tx_hash -> (when, JustHoot launch_requested without a token)
        self._started: dict[str, tuple[float, LaunchEvent]] = {}  # Nearly/JustHoot token -> its started
        self._emitted: OrderedDict[str, float] = OrderedDict()  # tokens whose started was already emitted (EMITTED_MAX)
        self._ready: dict[str, float] = {}  # ready already emitted (JustHoot sends dcl_positions_recorded twice)
        self._pools: dict[str, tuple[float, LaunchEvent]] = {}  # launch's DCL pool -> started (waiting for liquidity)
        self._funded: dict[str, float] = {}  # funding already emitted (token -> when)
        self._created: dict[str, float] = {}  # created already emitted

    def parse(self, block: dict) -> list[LaunchEvent]:
        height, block_ts = header(block)
        out: list[LaunchEvent] = []
        now = time.time()
        for o in outcomes(block):
            try:
                out += self._receipt(o.executor, o.receipt, o.outcome, o.tx_hash, height, now)
            except (KeyError, TypeError, ValueError, IndexError, AttributeError) as e:
                logger.debug("launch feed: receipt %s not parsed: %s", o.tx_hash, e)
        for memo in (self._intear_tx, self._hoot_req, self._started, self._pools):
            for k in [k for k, v in memo.items() if now - v[0] > TX_MEMORY_S]:
                memo.pop(k, None)
        for memo in (self._ready, self._funded, self._created):
            for k in [k for k, v in memo.items() if now - v > TX_MEMORY_S]:
                memo.pop(k, None)
        while len(self._emitted) > EMITTED_MAX:
            self._emitted.popitem(last=False)
        for e in out:
            e.block_ts, e.seen = block_ts, now
        return out

    def _receipt(self, ex: str, receipt: dict, outcome: dict, tx_hash: str | None, height: int,
                 now: float) -> list[LaunchEvent]:
        out = []
        evs = events(outcome.get("logs") or [])
        if ex in self._started:  # a receipt of the launch token itself: account creation, the launchpad's transfer into the pool
            out += self._token_receipt(ex, receipt, height, tx_hash, now)
        if ex and (ex == NEARLY or ex.endswith("." + NEARLY)):  # and future factories like v2.nearlytrade.near
            args = next((a.get("args") or {} for m, a in calls(receipt) if m == "launch"), {})
            # the tax and its split (tax_opts) are in the same receipt as launch_started: no get_tax call is needed
            taxes = {d.get("id"): {k: v for k, v in d.items() if k != "id"} for ev in evs
                     if str(ev.get("standard") or "").startswith("nearpad") and ev.get("event") == "tax_opts"
                     for d in items(ev.get("data"))}
            for ev in evs:
                if (not str(ev.get("standard") or "").startswith("nearpad")
                        or ev.get("event") not in ("launch_started", "launch")):
                    continue
                for d in items(ev.get("data")):
                    started = ev["event"] == "launch_started"
                    extra = {"dev_buy": d.get("dev_buy") or d.get("dev_buy_near"), "quote": d.get("quote")}
                    if started:
                        extra["links"] = from_nearly(args)
                        extra["launcher"] = ex
                        extra |= {k: d[k] for k in POINT_KEYS if isinstance(d.get(k), int)}
                        if d.get("id") in taxes:
                            extra["tax"] = taxes[d["id"]]
                    else:  # launch - in a receipt without arguments: links and tax from started
                        st = (self._started.pop(d.get("token"), None) or (0, None))[1]
                        if st:
                            extra["links"] = st.extra.get("links")
                            if st.extra.get("tax"):
                                extra["tax"] = st.extra["tax"]
                    e = LaunchEvent("nearly", "started" if started else "ready", d.get("token"), d.get("creator"),
                                    d.get("symbol"), d.get("name"), d.get("pool_id"), height, tx_hash, extra)
                    if started and e.token:
                        self._remember_started(e, now)
                    out.append(e)
        elif ex and (ex == JUSTHOOT_ROOT or ex.endswith("." + JUSTHOOT_ROOT)):
            out += self._hoot(ex, receipt, evs, tx_hash, height, now)
        elif ex == DCL:
            out += self._dcl(evs, receipt, tx_hash, height, now)
        elif ex == INTEAR_LAUNCHER:
            for method, args in calls(receipt):
                if method == "launch_token" and tx_hash:
                    creator = receipt.get("predecessor_id")
                    self._intear_tx[tx_hash] = (now, creator, args)
                    out.append(LaunchEvent("intear", "started", None, creator, args.get("symbol"), args.get("name"),
                                           None, height, tx_hash, {"links": from_intear(args)}))
        elif ex == INTEAR_ENGINE:
            out += self._intear_pool(evs, tx_hash, height)
        return out

    def _remember_started(self, e: LaunchEvent, now: float) -> None:
        self._started[e.token] = (now, e)
        self._emitted[e.token] = now
        if e.pool:
            self._pools[e.pool] = (now, e)

    def _hoot(self, ex: str, receipt: dict, evs: list[dict], tx_hash: str | None, height: int,
              now: float) -> list[LaunchEvent]:
        """JustHoot of any version: started - by the first hoot_launcher* event with token_id (v1 - launch_requested, v2 -
        launch_priced/launch_planned; creator, quote asset and launch_meme arguments - from launch_requested of the same
        transaction), ready - the first dcl_positions_recorded (v1 - from the token, v2 - from v2-pool-creator with token_id)."""
        out = []
        by_method = dict(calls(receipt))
        meme = (by_method.get("launch_meme") or {}).get("args") or {}
        pending = (by_method.get("on_quote_price") or {}).get("pending") or {}
        meta = pending.get("metadata") or {}
        found: dict[str, dict] = {}
        for ev in evs:
            std, name = str(ev.get("standard") or ""), ev.get("event")
            if not std.startswith("hoot_"):
                continue
            for d in items(ev.get("data")):
                if name == "dcl_positions_recorded":
                    out += self._hoot_ready(d.get("token_id") or ex, d, height, tx_hash, now)
                    continue
                if not std.startswith("hoot_launcher") or name not in HOOT_LAUNCH_EVENTS:
                    continue
                if name == "launch_requested" and not d.get("token_id") and tx_hash:  # v2: the token comes with the next receipt
                    self._hoot_req[tx_hash] = (now, {**d, "args": meme})
                    continue
                token = d.get("token_id")
                if not token or token in self._emitted or launchpad_of_account(token) != "justhoot":
                    continue
                info = found.setdefault(token, {})
                for k in ("creator_id", "quote_token_id", "dev_buy_quote_max", "dev_buy_quote"):
                    if d.get(k) is not None:
                        info.setdefault(k, d[k])
                plan = d.get("plan") or {}
                if plan.get("pool_id"):
                    info["pool"] = plan["pool_id"]
                    pos = plan.get("token_position") or {}
                    for k, v in (("init_point", plan.get("init_point")), ("left_point", pos.get("left_point")),
                                 ("right_point", pos.get("right_point"))):
                        if isinstance(v, int):
                            info[k] = v
        for token, info in found.items():
            req = (self._hoot_req.get(tx_hash or "") or (0, {}))[1]
            args = req.get("args") or meme
            creator = info.get("creator_id") or req.get("creator_id")
            quote = info.get("quote_token_id") or req.get("quote_token_id")
            pool = info.get("pool") or (pool_key(token, quote, LAUNCH_FEE) if quote else None)
            fee = args.get("transfer_fee_pips")  # the token's tax on transfers from / to the DCL (10000 = 1%)
            extra = {"dev_buy": info.get("dev_buy_quote_max") or req.get("dev_buy_quote_max")
                     or info.get("dev_buy_quote"), "quote": quote, "reference": args.get("reference"),
                     "launcher": ex, "fee_pips": fee if fee is not None else pending.get("transfer_fee_pips"),
                     "shares": args.get("shares")  # who gets the tax: token_owner_bps, holders_bps, burn_bps
                     } | {k: info[k] for k in POINT_KEYS if k in info}
            e = LaunchEvent("justhoot", "started", token, creator, args.get("symbol") or meta.get("symbol"),
                            args.get("name") or meta.get("name"), pool, height, tx_hash, extra)
            self._remember_started(e, now)
            out.append(e)
        return out

    def _hoot_ready(self, token: str, d: dict, height: int, tx_hash: str | None, now: float) -> list[LaunchEvent]:
        if token in self._ready:
            return []
        self._ready[token] = now
        lpts = d.get("lpt_ids") or []
        pool = lpts[0].split("#")[0] if lpts else None
        st = (self._started.pop(token, None) or (0, None))[1]
        return [LaunchEvent("justhoot", "ready", token, st and st.creator, st and st.symbol, st and st.name, pool,
                            height, tx_hash, dict(st.extra) if st else {})]

    def _token_receipt(self, token: str, receipt: dict, height: int, tx_hash: str | None,
                       now: float) -> list[LaunchEvent]:
        """A receipt of the launch token: created - the account is created (CreateAccount); funding - a launchpad account
        moves the tokens into the DCL (ft_transfer_call): liquidity follows in the next block. Nearly used to do it exactly
        at started+4, now it may be +10."""
        st = self._started[token][1]
        out = []
        actions = ((receipt.get("receipt") or {}).get("Action") or {}).get("actions") or []
        if token not in self._created and any(a == "CreateAccount" or isinstance(a, dict) and "CreateAccount" in a
                                               for a in actions):
            self._created[token] = now
            out.append(dataclasses.replace(st, stage="created", block=height, tx_hash=tx_hash, extra=dict(st.extra)))
        pred = receipt.get("predecessor_id") or ""
        if (token not in self._funded and launchpad_of_account(pred) and pred != token
                and any(m == "ft_transfer_call" and a.get("receiver_id") == DCL for m, a in calls(receipt))):
            self._funded[token] = now
            out.append(dataclasses.replace(st, stage="funding", block=height, tx_hash=tx_hash, extra=dict(st.extra)))
        return out

    def _dcl(self, evs: list[dict], receipt: dict, tx_hash: str | None, height: int, now: float) -> list[LaunchEvent]:
        """create_pool - the fallback path (a launch the launchpad parser did not recognise); liquidity_added for the pool of
        a waiting launch -> stage live (once per pool; on the fallback path also ready: it has no readiness event)."""
        out = []
        for ev in evs:
            if ev.get("standard") != "dcl.ref":
                continue
            name = str(ev.get("event") or "")
            for d in items(ev.get("data")):
                if name == "create_pool":
                    out += self._fallback(d, receipt, tx_hash, height, now)
                    continue
                if not name.startswith("liquidity_add") or not self._pools:
                    continue
                pool = d.get("pool_id") or str(d.get("lpt_id") or "").split("#")[0]
                hit = self._pools.pop(pool, None)
                if not hit:
                    continue
                st = hit[1]
                live = LaunchEvent(st.launchpad, "live", st.token, st.creator, st.symbol, st.name, pool, height,
                                   tx_hash, dict(st.extra))
                out.append(live)
                if st.extra.get("fallback") and st.token not in self._ready:
                    self._ready[st.token] = now
                    out.append(dataclasses.replace(live, stage="ready", extra=dict(st.extra)))
        return out

    def _fallback(self, d: dict, receipt: dict, tx_hash: str | None, height: int, now: float) -> list[LaunchEvent]:
        """create_pool: a launchpad account created a pool for its token. The launchpad is known but there was no started -
        the parser is out of date: started by the fallback path (creator - the transaction's signer). The token is an
        account of the same launchpad: the pool is not necessarily created by the token's parent (JustHoot v2:
        v2-pool-creator.justhoot.near for x.v2-launchpad.justhoot.near). An unknown launchpad is ignored."""
        account, pool = d.get("account_id") or "", d.get("pool_id") or ""
        parts = pool.split("|")[:2]
        lp = launchpad_of_account(account)
        if lp is None:
            return []
        if any(t in self._emitted or t in self._started for t in parts):
            return []  # a pool of a known launch - also when the pool's second token is a launchpad token too (HOOT)
        cands = [t for t in parts if t != account and launchpad_of_account(t) == lp]
        if len(cands) != 1:
            # both tokens belong to the launchpad (a launch paired with HOOT): which one is new cannot be told from the pool -
            # better to skip the launch than to report the pair asset (5 Nov: HOOTERS paired with HOOT gave a false HOOT launch)
            if cands:
                logger.warning("launch feed: create_pool %s by %s - both tokens are the launchpad's, no launch emitted", pool,
                               account)
            return []
        token = cands[0]
        quote = next((t for t in parts if t != token), None)
        extra = {"fallback": True, "launcher": account, "quote": quote}
        if isinstance(d.get("init_point"), int):
            extra["init_point"] = d["init_point"]
        e = LaunchEvent(lp, "started", token, signer(receipt), None, None, pool, height, tx_hash, extra)
        self._remember_started(e, now)
        logger.warning("launch feed: launch %s found only by create_pool (launcher %s) - the parser is out of date", token,
                       account)
        return [e]

    def _intear_pool(self, evs: list[dict], tx_hash: str | None, height: int) -> list[LaunchEvent]:
        """pool_updated with LaunchV2 inside the launch_token transaction (the pool is created and the dev buy is done)."""
        known = self._intear_tx.get(tx_hash or "")
        if not known:
            return []
        for ev in evs:
            found = _intear_launch_pool(ev)
            if found is None:
                continue
            pool_id, quote, launched = found
            _, creator, args = known
            self._intear_tx.pop(tx_hash, None)
            return [LaunchEvent("intear", "ready", launched.removeprefix("nep141:") or None, creator, args.get("symbol"),
                                args.get("name"), pool_id, height, tx_hash,
                                {"quote": quote, "links": from_intear(args)})]
        return []
