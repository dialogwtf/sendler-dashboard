"""Holders of launch tokens for Sendler Dash: token × account balances from the block stream's NEP-141 events.

Tokens of Nearly / JustHoot / Intear launches emit `ft_mint` (the whole supply, the dev buy), `ft_transfer` (the tax
too, as a separate entry with memo `tax`) and `ft_burn` - the sum of events since the mint is the balance. So:

- the worker accumulates increments (token, account) -> Σ between flushes and writes `$inc` to `dash_holders`
  (Decimal128: u128 does not fit int64, and balances must be sortable). A repeated block is harmless: the worker never
  takes a height twice;
- a token whose first seen event is `ft_mint` is complete (`complete: true` in `dash_holder_stats`): every transfer
  of it is known. Tokens launched before the collection started have only increments -
  `scripts/backfill_dash_holders.py` reconciles them with a FastNear snapshot (`seeded`); until then the API hides
  their holders;
- every STATS_S, for tokens with transfers - the holder count (balance > 0) and an hourly series point (`series`, 30 days).

Shares (top 10, dev, linked, contracts) are computed by the API from the top of `dash_holders`: it has labels and the creator.
Pure functions - no database, no network; writing - HolderBook.drain() -> operations for bulk_write.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass

from bson.decimal128 import Decimal128
from pymongo import UpdateOne

from services.chain.block import events, items, outcomes

FT_STANDARD = "nep141"
SERIES_POINTS = 24 * 30  # hourly points of the holder count
STATS_S = 60.0


@dataclass(frozen=True)
class Delta:
    token: str
    account: str
    amount: int  # signed, raw units
    mint: bool = False


def ft_deltas(block: dict, tokens) -> list[Delta]:
    """Balance changes of tokens from `tokens` in a block (successful receipts only - the logs of a reverted receipt
    did not change balances)."""
    out: list[Delta] = []
    for o in outcomes(block):
        if o.executor not in tokens:
            continue
        for ev in events(o.logs):
            if ev.get("standard") != FT_STANDARD:
                continue
            name = ev.get("event")
            for d in items(ev.get("data")):
                try:
                    amount = int(d.get("amount") or 0)
                except (TypeError, ValueError):
                    continue
                if amount <= 0:
                    continue
                if name == "ft_transfer":
                    frm, to = d.get("old_owner_id"), d.get("new_owner_id")
                    if not isinstance(frm, str) or not isinstance(to, str) or frm == to:
                        continue
                    out.append(Delta(o.executor, frm, -amount))
                    out.append(Delta(o.executor, to, amount))
                elif name == "ft_mint" and isinstance(d.get("owner_id"), str):
                    out.append(Delta(o.executor, d["owner_id"], amount, mint=True))
                elif name == "ft_burn" and isinstance(d.get("owner_id"), str):
                    out.append(Delta(o.executor, d["owner_id"], -amount))
    return out


def holder_id(token: str, account: str) -> str:
    return f"{token}:{account}"


def dec(n: int) -> Decimal128:
    return Decimal128(str(n))


class HolderBook:
    """Balance increments between flushes + which tokens changed (to recount the holders)."""

    def __init__(self) -> None:
        self.tokens: set[str] = set()  # launch tokens whose balances are tracked
        self._acc: dict[tuple[str, str], int] = defaultdict(int)
        self._first: dict[str, bool] = {}  # a token without recorded stats -> its first event was a mint
        self.dirty: set[str] = set()

    def track(self, token: str) -> None:
        if token:
            self.tokens.add(token)

    def add(self, block: dict) -> int:
        n = 0
        for d in ft_deltas(block, self.tokens):
            self._acc[(d.token, d.account)] += d.amount
            self._first.setdefault(d.token, d.mint)
            self.dirty.add(d.token)
            n += 1
        return n

    def drain(self, at: dt.datetime) -> list[UpdateOne]:
        acc, self._acc = self._acc, defaultdict(int)
        return [UpdateOne({"_id": holder_id(t, a)},
                          {"$inc": {"bal": dec(v)}, "$set": {"updated_at": at},
                           "$setOnInsert": {"token": t, "account": a, "first_at": at}}, upsert=True)
                for (t, a), v in acc.items() if v]

    def take_dirty(self) -> tuple[set[str], dict[str, bool]]:
        """Tokens with transfers since the last recount and the "first event is a mint" flag (for $setOnInsert)."""
        dirty, self.dirty = self.dirty, set()
        first = {t: self._first.pop(t) for t in dirty if t in self._first}
        return dirty, first


def stats_op(token: str, holders: int, at: dt.datetime, minted: bool | None) -> UpdateOne:
    """dash_holder_stats: the holder count and the hourly series - the hour's last point is replaced, a new hour is
    appended (an update pipeline, no read). `complete` is set once, on the first write."""
    hour = at.replace(minute=0, second=0, microsecond=0)
    point = {"t": hour, "n": holders}
    series = {"$ifNull": ["$series", []]}
    last_t = {"$arrayElemAt": [{"$map": {"input": series, "in": "$$this.t"}}, -1]}
    pipeline = [{"$set": {
        "holders": holders, "updated_at": at,
        "complete": {"$ifNull": ["$complete", bool(minted)]},
        "series": {"$slice": [{"$cond": [
            {"$eq": [last_t, hour]},
            {"$concatArrays": [{"$slice": [series, {"$max": [0, {"$subtract": [{"$size": series}, 1]}]}]}, [point]]},
            {"$concatArrays": [series, [point]]}]}, -SERIES_POINTS]},
    }}]
    return UpdateOne({"_id": token}, pipeline, upsert=True)


# ── reconciliation with a snapshot (scripts/backfill_dash_holders.py) ────────

SNAPSHOT_LIMIT = 100  # FastNear /v1/ft/<token>/top returns at most 100 holders


def corrections(stored: dict[str, int], snapshot: dict[str, int], full: bool = True) -> dict[str, int]:
    """What to add to the recorded balances so that they match the snapshot: correction = snapshot - recorded, so a
    repeated reconciliation is safe. A full snapshot (FastNear returned fewer than SNAPSHOT_LIMIT entries) is all the
    holders: the other recorded ones get zero. A cut-off one - for the rest only a non-negative balance is known (they
    sold what they had bought before the collection started)."""
    out: dict[str, int] = {}
    for acc, bal in snapshot.items():
        if (d := bal - stored.get(acc, 0)) != 0:
            out[acc] = d
    for acc, bal in stored.items():
        if acc in snapshot:
            continue
        if bal != 0 and (full or bal < 0):
            out[acc] = -bal
    return out
