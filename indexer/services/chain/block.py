"""Parsing of a neardata block, shared by every subscriber of the stream: contract accounts, receipts, EVENT_JSON
events, method calls.

The module has no dependencies inside the project: everything imports it, it imports nothing.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Iterator

WNEAR = "wrap.near"
DCL = "dclv2.ref-labs.near"
REF = "v2.ref-finance.near"
INTEAR_ENGINE = "dex.intear.near"
NEARLY = "nearlytrade.near"
INTENTS = "intents.near"
USDC = "17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1"
USDT = "usdt.tether-token.near"
STABLES = {USDC: 6, USDT: 6}  # ft_prices may have no rate for them - counted as $1
SYSTEM = "system"  # predecessor of gas refunds and of a failed receipt's deposit refund
EVENT_PREFIX = "EVENT_JSON:"


def pool_key(a: str, b: str, fee: int) -> str:
    """Rhea DCL pool id: tokens in alphabetical order and the fee in hundredths of a percent."""
    x, y = sorted((a, b))
    return f"{x}|{y}|{fee}"


def events(logs) -> list[dict]:
    """EVENT_JSON events from a receipt's logs (broken lines are skipped)."""
    out = []
    for line in logs or []:
        if isinstance(line, str) and line.startswith(EVENT_PREFIX):
            try:
                ev = json.loads(line[len(EVENT_PREFIX):])
            except ValueError:
                continue
            if isinstance(ev, dict):
                out.append(ev)
    return out


def items(data) -> list[dict]:
    """An event's data - an object or a list of objects; always returns a list of dicts."""
    return [d for d in (data if isinstance(data, list) else [data]) if isinstance(d, dict)]


def actions(receipt: dict) -> list:
    return ((((receipt.get("receipt") or {}).get("Action")) or {}).get("actions")) or []


def signer(receipt: dict) -> str | None:
    """Signer of the receipt's original transaction."""
    return (((receipt.get("receipt") or {}).get("Action")) or {}).get("signer_id")


def calls(receipt: dict) -> list[tuple[str, dict]]:
    """FunctionCall actions of a receipt: [(method, args)]; args that are not JSON - {}."""
    out = []
    for a in actions(receipt):
        fc = a.get("FunctionCall") if isinstance(a, dict) else None
        if not fc:
            continue
        try:
            args = json.loads(base64.b64decode(fc.get("args") or ""))
        except (ValueError, TypeError):
            args = {}
        out.append((fc.get("method_name"), args if isinstance(args, dict) else {}))
    return out


def header(block: dict) -> tuple[int, float]:
    """(height, block time in unix seconds; 0 - none)."""
    h = (block.get("block") or {}).get("header") or {}
    return int(h.get("height") or 0), int(h.get("timestamp") or 0) / 1e9


@dataclass
class Outcome:
    """An executed receipt of a block."""
    executor: str | None
    receipt: dict
    outcome: dict
    tx_hash: str | None

    @property
    def logs(self) -> list[str]:
        return self.outcome.get("logs") or []

    @property
    def receipt_id(self) -> str | None:
        return self.receipt.get("receipt_id")

    @property
    def predecessor(self) -> str | None:
        return self.receipt.get("predecessor_id")


def outcomes(block: dict) -> Iterator[Outcome]:
    """Successful receipts of a block (SuccessValue / SuccessReceiptId) in shard order."""
    for shard in block.get("shards") or []:
        for reo in shard.get("receipt_execution_outcomes") or []:
            outcome = (reo.get("execution_outcome") or {}).get("outcome") or {}
            status = outcome.get("status") or {}
            if "SuccessValue" not in status and "SuccessReceiptId" not in status:
                continue
            receipt = reo.get("receipt") or {}
            yield Outcome(outcome.get("executor_id") or receipt.get("receiver_id"), receipt, outcome,
                          reo.get("tx_hash"))
