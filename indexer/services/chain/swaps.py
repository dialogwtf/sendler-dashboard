"""Swaps from the block stream - shared parsing for the dashboard's trades and for smart money.

A swap is visible in the venue's receipt:
- Rhea DCL (`dclv2.ref-labs.near`) - event `dcl.ref` `swap` (`swapper`, `pool_id`, `token_in/out`, `amount_in/out`);
- Intear DEX (`dex.intear.near`) - event `inteardex` `swap` (`trader`, `dex_id`, `request.asset_in/out`,
  `request.message` - the pool number, u32 little-endian); the nested `dex_event` of the same swap is not counted;
- Rhea v1 (`v2.ref-finance.near`) - log `Swapped A X for B Y`; the wallet is `sender_id` of `ft_on_transfer` or the
  `predecessor` of a direct `swap`; the pool is `pool_id` from the `actions` arguments, in log order.

`receipt_swaps` - swaps of one receipt; `swap_legs` - the same as (who, leg) pairs for smart money;
`block_swaps` - all swaps of a block with the receipt, transaction, signer and time - for the dashboard. No state:
netting the legs of one transaction (aggregator routes) is up to the consumer (`net_flow`).
"""
from __future__ import annotations

import base64
import json
import re
import struct
from dataclasses import dataclass
from typing import Iterator

from .block import (
    DCL,
    INTEAR_ENGINE,
    REF,
    USDC,
    USDT,
    calls,
    events,
    header,
    items,
    outcomes,
    signer,
)

VENUES = {DCL: "Rhea DCL", INTEAR_ENGINE: "Intear DEX", REF: "Rhea"}
NEAR = "near"  # NEAR and wNEAR are one asset
KNOWN = {NEAR: ("NEAR", 24), USDC: ("USDC", 6), USDT: ("USDT", 6),
         "a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48.factory.bridge.near": ("USDC.e", 6),
         "dac17f958d2ee523a2206206994597c13d831ec7.factory.bridge.near": ("USDT.e", 6)}
QUOTES = frozenset(KNOWN)  # receiving them is not a buy (a sell, or a stablecoin swap)
REF_SWAPPED = re.compile(r"^Swapped (\d+) (\S+) for (\d+) ([^\s,]+)")
REF_ACTION_METHODS = ("swap", "swap_by_output", "execute_actions")


@dataclass
class Leg:
    token_in: str
    amount_in: int
    token_out: str
    amount_out: int
    venue: str


@dataclass(frozen=True)
class Swap:
    """One swap of a venue's receipt."""
    venue: str  # VENUES[executor]
    pool: str | None  # DCL - "x|y|fee"; Intear - "dex_id#number"; Rhea v1 - "v2.ref-finance.near#number"; None - unknown
    actor: str | None  # who swaps: swapper / trader / sender_id; for an aggregator - the aggregator itself
    token_in: str  # unified asset id: NEAR and wNEAR - "near"
    amount_in: int
    token_out: str
    amount_out: int
    index: int  # number of the swap in the receipt

    @property
    def leg(self) -> Leg:
        return Leg(self.token_in, self.amount_in, self.token_out, self.amount_out, self.venue)


@dataclass(frozen=True)
class BlockSwap:
    """A swap with its block context."""
    swap: Swap
    receipt_id: str | None
    tx_hash: str | None
    signer: str | None  # signer of the transaction
    height: int
    ts: float  # block time, unix seconds

    @property
    def key(self) -> str:
        """Unique id of the swap: the receipt and the number in it."""
        return f"{self.receipt_id}:{self.swap.index}"


def asset(a: str | None) -> str:
    """wrap.near, near, nep141:x -> unified asset id."""
    a = (a or "").removeprefix("nep141:")
    return NEAR if a in ("near", "wrap.near") else a


def intear_pool(dex_id: str | None, message: str | None) -> str | None:
    """Intear pool: dex_id and the number from request.message (base64 u32 LE); another format - dex_id only."""
    if not dex_id:
        return None
    try:
        raw = base64.b64decode(message or "", validate=True)
    except (ValueError, TypeError):
        return dex_id
    return f"{dex_id}#{struct.unpack('<I', raw)[0]}" if len(raw) == 4 else dex_id


def ref_pools(receipt: dict) -> list:
    """pool_id of Rhea v1 actions from the receipt's arguments (swap / execute_actions / ft_on_transfer.msg), in order."""
    for method, args in calls(receipt):
        acts = None
        if method in REF_ACTION_METHODS:
            acts = args.get("actions")
        elif method == "ft_on_transfer":
            try:
                msg = json.loads(args.get("msg") or "")
            except (ValueError, TypeError):
                msg = None
            acts = msg.get("actions") if isinstance(msg, dict) else None
        if isinstance(acts, list):
            return [a.get("pool_id") if isinstance(a, dict) else None for a in acts]
    return []


def receipt_swaps(executor: str, receipt: dict, outcome: dict) -> list[Swap]:
    """Swaps of a venue's receipt. Broken amounts - ValueError/TypeError: the caller skips the whole receipt."""
    venue = VENUES.get(executor)
    if venue is None:
        return []
    out: list[Swap] = []
    logs = outcome.get("logs") or []
    if executor == REF:
        sender = next((a.get("sender_id") for m, a in calls(receipt) if m == "ft_on_transfer"), None)
        actor = sender or receipt.get("predecessor_id")
        matches = [m for m in (REF_SWAPPED.match(line) for line in logs) if m]
        pools = ref_pools(receipt) if matches else []
        if len(pools) != len(matches):  # the number of actions does not match the logs - the pool is not guessed
            pools = [None] * len(matches)
        for i, (m, pid) in enumerate(zip(matches, pools)):
            out.append(Swap(venue, f"{REF}#{pid}" if pid is not None else None, actor,
                            asset(m.group(2)), int(m.group(1)), asset(m.group(4)), int(m.group(3)), i))
        return out
    for ev in events(logs):
        if executor == DCL and (ev.get("standard"), ev.get("event")) == ("dcl.ref", "swap"):
            for d in items(ev.get("data")):
                out.append(Swap(venue, d.get("pool_id"), d.get("swapper"),
                                asset(d.get("token_in")), int(d.get("amount_in") or 0),
                                asset(d.get("token_out")), int(d.get("amount_out") or 0), len(out)))
        elif executor == INTEAR_ENGINE and (ev.get("standard"), ev.get("event")) == ("inteardex", "swap"):
            for d in items(ev.get("data")):
                req = d.get("request") or {}
                out.append(Swap(venue, intear_pool(d.get("dex_id"), req.get("message")), d.get("trader"),
                                asset(req.get("asset_in")), int(d.get("amount_in") or 0),
                                asset(req.get("asset_out")), int(d.get("amount_out") or 0), len(out)))
    return out


def swap_legs(executor: str, receipt: dict, outcome: dict) -> list[tuple[str | None, Leg]]:
    """Swaps of a venue's receipt: [(who swaps, leg)]."""
    return [(s.actor, s.leg) for s in receipt_swaps(executor, receipt, outcome)]


def net_flow(legs: list[Leg]) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    """Net flow over the legs -> (received, given); a route's intermediate assets cancel out."""
    net: dict[str, int] = {}
    for leg in legs:
        net[leg.token_in] = net.get(leg.token_in, 0) - leg.amount_in
        net[leg.token_out] = net.get(leg.token_out, 0) + leg.amount_out
    return [(t, a) for t, a in net.items() if a > 0], [(t, -a) for t, a in net.items() if a < 0]


def block_swaps(block: dict) -> Iterator[BlockSwap]:
    """All swaps of a block in receipt order. A receipt with broken amounts is skipped as a whole."""
    height, ts = header(block)
    for o in outcomes(block):
        if o.executor not in VENUES:
            continue
        try:
            swaps = receipt_swaps(o.executor, o.receipt, o.outcome)
        except (KeyError, TypeError, ValueError):
            continue
        tx_signer = signer(o.receipt)
        for s in swaps:
            yield BlockSwap(s, o.receipt_id, o.tx_hash, tx_signer, height, ts)
