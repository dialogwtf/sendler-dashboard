"""What else the dashboard takes from a block besides swaps - pure functions, no database, no network.

- transfers: NEAR - a receipt with a Transfer action (gas and deposit refunds from `system` are not counted); tokens -
  NEP-141 `ft_transfer` events (of any contract that emits them);
- network: transactions, receipts, gas, burnt NEAR, new accounts (CreateAccount), transaction signers;
- new items: a new contract - CreateAccount + DeployContract in one receipt (a redeploy of an old account is not
  new); a pool - create_pool in Rhea DCL, add_simple_pool / add_stable_swap_pool in Rhea v1 and PoolUpdated with a new
  number in Intear (intear_pools; the worker decides what is new). The type of a new contract
  (token or NFT) is refined by the worker from its first nep141 / nep171 events.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field

from services.chain.block import (
    DCL,
    INTEAR_ENGINE,
    REF,
    SYSTEM,
    actions,
    calls,
    events,
    header,
    items,
    outcomes,
    signer,
)

FT_STANDARD = "nep141"
NFT_STANDARD = "nep171"


@dataclass(frozen=True)
class Transfer:
    token: str  # "near" - native NEAR, otherwise the token's contract
    amount: int  # raw units
    sender: str | None
    receiver: str | None
    receipt_id: str | None
    tx_hash: str | None
    signer: str | None
    index: int  # number in the receipt

    @property
    def key(self) -> str:
        return f"{self.receipt_id}:t{self.index}"


@dataclass
class NetStats:
    txs: int = 0
    receipts: int = 0
    gas: int = 0  # gas of the receipts, gas units
    burnt: int = 0  # burnt NEAR, yocto
    new_accounts: int = 0
    signers: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Discovery:
    kind: str  # contract | pool
    id: str
    by: str | None
    tx_hash: str | None
    extra: dict = field(default_factory=dict, hash=False, compare=False)


def _action_kinds(receipt: dict) -> set[str]:
    out = set()
    for a in actions(receipt):
        out.add(a if isinstance(a, str) else next(iter(a), ""))
    return out


def transfers(block: dict) -> list[Transfer]:
    """NEAR and NEP-141 transfers of a block (successful receipts only)."""
    out: list[Transfer] = []
    for o in outcomes(block):
        pred = o.predecessor
        tx_signer = signer(o.receipt)
        i = 0
        if pred and pred != SYSTEM:
            for a in actions(o.receipt):
                if isinstance(a, dict) and "Transfer" in a:
                    try:
                        amount = int((a["Transfer"] or {}).get("deposit") or 0)
                    except (TypeError, ValueError):
                        continue
                    if amount > 0:
                        out.append(Transfer("near", amount, pred, o.executor, o.receipt_id, o.tx_hash, tx_signer, i))
                        i += 1
        for ev in events(o.logs):
            if ev.get("standard") != FT_STANDARD or ev.get("event") != "ft_transfer":
                continue
            for d in items(ev.get("data")):
                try:
                    amount = int(d.get("amount") or 0)
                except (TypeError, ValueError):
                    continue
                if amount > 0:
                    out.append(Transfer(o.executor, amount, d.get("old_owner_id"), d.get("new_owner_id"),
                                        o.receipt_id, o.tx_hash, tx_signer, i))
                    i += 1
    return out


def net_stats(block: dict) -> NetStats:
    s = NetStats()
    for shard in block.get("shards") or []:
        for t in (shard.get("chunk") or {}).get("transactions") or []:
            s.txs += 1
            who = (t.get("transaction") or {}).get("signer_id")
            if who:
                s.signers.append(who)
        for reo in shard.get("receipt_execution_outcomes") or []:
            o = (reo.get("execution_outcome") or {}).get("outcome") or {}
            s.receipts += 1
            try:
                s.gas += int(o.get("gas_burnt") or 0)
                s.burnt += int(o.get("tokens_burnt") or 0)
            except (TypeError, ValueError):
                pass
    for o in outcomes(block):
        if "CreateAccount" in _action_kinds(o.receipt):
            s.new_accounts += 1
    return s


def _return_value(outcome: dict):
    raw = (outcome.get("status") or {}).get("SuccessValue")
    if not raw:
        return None
    try:
        return json.loads(base64.b64decode(raw))
    except (ValueError, TypeError):
        return None


def discoveries(block: dict) -> list[Discovery]:
    out: list[Discovery] = []
    for o in outcomes(block):
        kinds = _action_kinds(o.receipt)
        if "CreateAccount" in kinds and kinds & {"DeployContract", "UseGlobalContract", "DeployGlobalContract"}:
            out.append(Discovery("contract", o.executor, o.predecessor, o.tx_hash))
        if o.executor == DCL:
            for ev in events(o.logs):
                if (ev.get("standard"), ev.get("event")) != ("dcl.ref", "create_pool"):
                    continue
                for d in items(ev.get("data")):
                    pool = d.get("pool_id")
                    if isinstance(pool, str) and pool.count("|") == 2:
                        out.append(Discovery("pool", pool, d.get("account_id") or signer(o.receipt), o.tx_hash,
                                             {"venue": "Rhea DCL", "tokens": pool.split("|")[:2]}))
            continue
        if o.executor != REF:
            continue
        for method, args in calls(o.receipt):
            if method in ("add_simple_pool", "add_stable_swap_pool", "add_rated_swap_pool"):
                pid = _return_value(o.outcome)
                if isinstance(pid, int):
                    out.append(Discovery("pool", f"{REF}#{pid}", signer(o.receipt), o.tx_hash,
                                         {"venue": "Rhea", "tokens": [t for t in args.get("tokens") or []
                                                                      if isinstance(t, str)]}))
    return out


INTEAR_XYK = "slimedragon.near/xyk"  # dex_id of Intear pools - as in the swap's pool id (services/chain/swaps.intear_pool)


@dataclass(frozen=True)
class IntearPool:
    pool_id: int
    kind: str  # private | public | launch | launch_v2
    tokens: tuple[str, ...]
    tx_hash: str | None
    by: str | None


def intear_pools(block: dict) -> list[IntearPool]:
    """Intear pools changed in the block (event dex_events_borsh -> PoolUpdated). Whether a pool is new is decided by the
    worker: pool numbers are consecutive, a new one is above all known. A broken event is skipped."""
    from services.chain.intear import pool_updates

    out: list[IntearPool] = []
    for o in outcomes(block):
        if o.executor != INTEAR_ENGINE:
            continue
        for ev in events(o.logs):
            if (ev.get("standard"), ev.get("event")) != ("inteardex", "dex_events_borsh"):
                continue
            try:
                pools = pool_updates(ev.get("data"))
            except (ValueError, TypeError):
                continue
            for p in pools:
                tokens = tuple("near" if a == "near" else a.removeprefix("nep141:") for a in p.assets)
                out.append(IntearPool(p.pool_id, p.kind, tokens, o.tx_hash, signer(o.receipt)))
    return out


def token_standards(block: dict) -> list[tuple[str, str]]:
    """(contract, nep141 | nep171) - who emits the standard's events in the block; the worker refines new contracts' type."""
    out = []
    for o in outcomes(block):
        for ev in events(o.logs):
            std = ev.get("standard")
            if std in (FT_STANDARD, NFT_STANDARD) and o.executor:
                out.append((o.executor, std))
                break
    return out


def block_time(block: dict) -> float:
    return header(block)[1]
