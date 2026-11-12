"""Network metrics per minute, hour and day (Sendler Dash): blocks, transactions, receipts, gas, burnt NEAR, new
accounts and unique signers.

Unique signers per hour and day - HyperLogLog (4096 registers, error ~1.6%): hundreds of thousands of accounts a day
are not kept as a set in the process's memory. Registers of the current hour and day are written to the document and
read back on restart - the count continues. Per minute there are few signers - an exact set.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import math

from pymongo import UpdateOne

from .blocks import NetStats

P = 12
M = 1 << P
ALPHA = 0.7213 / (1 + 1.079 / M)
BUCKETS = {"1m": 60, "1h": 3600, "1d": 86400}
TTL_S = {"1m": 7 * 86400, "1h": None, "1d": None}


class HyperLogLog:
    def __init__(self, registers: bytes | None = None) -> None:
        self.r = bytearray(registers) if registers and len(registers) == M else bytearray(M)

    def add(self, item: str) -> None:
        h = int.from_bytes(hashlib.blake2b(item.encode(), digest_size=8).digest(), "big")
        idx = h >> (64 - P)
        rest = h & ((1 << (64 - P)) - 1)
        rank = (64 - P) - rest.bit_length() + 1
        if rank > self.r[idx]:
            self.r[idx] = rank

    def count(self) -> int:
        z = sum(2.0 ** -v for v in self.r)
        est = ALPHA * M * M / z
        zeros = self.r.count(0)
        if est <= 2.5 * M and zeros:
            est = M * math.log(M / zeros)
        return int(round(est))


class _Bucket:
    __slots__ = ("blocks", "txs", "receipts", "gas", "burnt", "new_accounts", "signers")

    def __init__(self) -> None:
        self.blocks = self.txs = self.receipts = self.gas = self.burnt = self.new_accounts = 0
        self.signers: set[str] | HyperLogLog | None = None


class NetworkAgg:
    """Accumulates increments per bucket; drain() -> UpdateOne for dash_network (`_id` = "1m:<start>")."""

    def __init__(self) -> None:
        self._delta: dict[tuple[str, int], _Bucket] = {}
        self._hll: dict[tuple[str, int], HyperLogLog] = {}  # the current hour and day - live between flushes
        self._minute_signers: dict[int, set[str]] = {}

    def restore(self, name: str, start: int, registers: bytes | None) -> None:
        self._hll[(name, start)] = HyperLogLog(registers)

    def add(self, ts: float, s: NetStats) -> None:
        for name, step in BUCKETS.items():
            start = int(ts // step * step)
            b = self._delta.setdefault((name, start), _Bucket())
            b.blocks += 1
            b.txs += s.txs
            b.receipts += s.receipts
            b.gas += s.gas
            b.burnt += s.burnt
            b.new_accounts += s.new_accounts
            if name == "1m":
                self._minute_signers.setdefault(start, set()).update(s.signers)
            else:
                hll = self._hll.get((name, start))
                if hll is None:
                    hll = self._hll[(name, start)] = HyperLogLog()
                for who in s.signers:
                    hll.add(who)

    def known(self, name: str, start: int) -> bool:
        return (name, start) in self._hll

    def drain(self, now_ts: float) -> list[UpdateOne]:
        ops = []
        for (name, start), b in self._delta.items():
            at = dt.datetime.fromtimestamp(start, dt.timezone.utc)
            on_insert = {"i": name, "t": at}
            if TTL_S[name]:
                on_insert["expires_at"] = at + dt.timedelta(seconds=TTL_S[name])
            upd = {"$setOnInsert": on_insert,
                   "$inc": {"blocks": b.blocks, "txs": b.txs, "receipts": b.receipts, "tgas": b.gas / 1e12,
                            "burnt_near": b.burnt / 1e24, "new_accounts": b.new_accounts}}
            if name == "1m":
                upd["$set"] = {"signers": len(self._minute_signers.get(start, ()))}
            else:
                hll = self._hll[(name, start)]
                upd["$set"] = {"signers": hll.count(), "hll": bytes(hll.r)}
            ops.append(UpdateOne({"_id": f"{name}:{start}"}, upd, upsert=True))
        self._delta.clear()
        # closed buckets are no longer needed in memory
        for key in [k for k in self._hll if k[1] + BUCKETS[k[0]] < now_ts - 60]:
            self._hll.pop(key)
        for start in [m for m in self._minute_signers if m + 60 < now_ts - 60]:
            self._minute_signers.pop(start)
        return ops
