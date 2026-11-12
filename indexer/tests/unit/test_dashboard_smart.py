"""Smart money (services/dashboard/smart.py): trades from the stream's swaps, aggregator -> signer, exclusions,
the profit formula and the selection of "smart" wallets.

Swaps are in the mainnet event format: dcl.ref swap (swapper, token_in/out, amount_in/out)."""
import asyncio
import json
import time

import pytest

from services.chain.block import DCL
from services.chain.swaps import Leg
from services.dashboard.smart import MIN_TOKENS, NEAR, SmartMoney, SmartTracker, score, token_pnl, trade_of

TOK = "dog.nearlytrade.near"
USDC = "17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1"
T18 = 10 ** 18


def run(coro):
    return asyncio.run(coro)


def ev(data):
    return "EVENT_JSON:" + json.dumps({"standard": "dcl.ref", "version": "1.0.0", "event": "swap", "data": data})


def swap(swapper, t_in, a_in, t_out, a_out, pool=f"{TOK}|wrap.near|10000"):
    return {"pool_id": pool, "swapper": swapper, "token_in": t_in, "token_out": t_out, "amount_in": str(a_in),
            "amount_out": str(a_out)}


def block(height, receipts):
    """receipts: [(tx, signer, [swap…])]"""
    reos = [{"tx_hash": tx, "receipt": {"receipt_id": f"r{tx}", "predecessor_id": sig,
                                        "receipt": {"Action": {"signer_id": sig, "actions": []}}},
             "execution_outcome": {"outcome": {"executor_id": DCL, "status": {"SuccessValue": ""},
                                               "logs": [ev(s)]}}} for tx, sig, s in receipts]
    return {"block": {"header": {"height": height, "timestamp": int(time.time() * 1e9)}},
            "shards": [{"receipt_execution_outcomes": reos}]}


def settle(tracker, start, blocks):
    out = []
    for i, b in enumerate(blocks):
        out += tracker.parse(block(start + i, b))
    for i in range(5):
        out += tracker.parse(block(start + len(blocks) + i, []))
    return out


# ── trades from the stream ───────────────────────────────────────────────────

def test_buy_and_sell_from_dcl_swaps():
    t = SmartTracker()
    got = settle(t, 100, [[("T1", "alice.near", [swap("alice.near", "wrap.near", NEAR, TOK, 1000 * T18)])],
                          [("T2", "alice.near", [swap("alice.near", TOK, 400 * T18, "wrap.near", 2 * NEAR)])]])
    assert [(x.wallet, x.side, x.near, x.amount) for x in got] == [
        ("alice.near", "buy", NEAR, 1000 * T18), ("alice.near", "sell", 2 * NEAR, 400 * T18)]


def test_route_through_usdc_is_one_trade():
    legs = [Leg("near", NEAR, USDC, 3_000_000, "Rhea DCL"), Leg(USDC, 3_000_000, TOK, 500 * T18, "Rhea DCL")]
    tr = trade_of("bob.near", "T", 0.0, legs)
    assert (tr.side, tr.token, tr.near, tr.amount) == ("buy", TOK, NEAR, 500 * T18)
    assert trade_of("bob.near", "T", 0.0, [Leg("near", NEAR, USDC, 3_000_000, "Rhea DCL")]) is None  # a stable


def test_aggregator_with_many_signers_attributes_to_signer():
    t = SmartTracker()
    agg = "router.aggr.near"
    blocks = [[(f"T{i}", f"user{i}.near", [swap(agg, "wrap.near", NEAR, TOK, 10 * T18)])] for i in range(5)]
    got = settle(t, 200, blocks)
    # the first trades - until the actor is recognised as a contract; then - to the signer
    assert {x.wallet for x in got[3:]} == {"user3.near", "user4.near"}


class FakeCol:
    def __init__(self):
        self.ops = []

    async def bulk_write(self, ops, ordered=False):
        self.ops += [op._doc["$set"] for op in ops]


class FakeDb(dict):
    def __missing__(self, key):
        self[key] = FakeCol()
        return self[key]


def test_contracts_and_creator_are_skipped():
    sm = SmartMoney(FakeDb(), creator=lambda t: "dev.near")
    b = [("T1", "x.near", [swap("dclv2.ref-labs.near", "wrap.near", NEAR, TOK, T18)]),
         ("T2", "dev.near", [swap("dev.near", TOK, T18, "wrap.near", NEAR)]),
         ("T3", "carol.near", [swap("carol.near", "wrap.near", NEAR, TOK, T18)])]

    async def feed():
        for i in range(6):
            await sm.handle(block(300 + i, b if i == 0 else []))
        await sm.drain()

    run(feed())
    assert [d["wallet"] for d in sm.db["sniper_smart_flows"].ops] == ["carol.near"]


# ── scoring ──────────────────────────────────────────────────────────────────

def test_pnl_ignores_tokens_without_cost_and_marks_rest():
    # put 1 NEAR into 1000, sold 500 for 1.5 NEAR, 500 left at 0.002 NEAR -> +1 + 0.5
    f = {"near_in": 1.0, "near_out": 1.5, "tok_in": 1000.0, "tok_out": 500.0}
    assert token_pnl(f, 0.002 * NEAR) == pytest.approx(1.0 + 1.0 - 0.5)
    # a dev: bought 100 tokens for 0.2 NEAR, sold 10 000 (allocation) for 50 NEAR - only what was bought counts
    dev = {"near_in": 0.2, "near_out": 50.0, "tok_in": 100.0, "tok_out": 10_000.0}
    assert token_pnl(dev, 0) == pytest.approx(50.0 * 100 / 10_000 - 0.2)
    assert token_pnl({"near_in": 0.05, "tok_in": 1.0}, 0) is None  # dust is not scored


def flows(wallet, results):
    """results: profit per token for an entry of 1 NEAR (everything sold)."""
    return [{"wallet": wallet, "token": f"t{i}.near", "near_in": 1.0, "near_out": 1.0 + r, "tok_in": 100.0,
             "tok_out": 100.0} for i, r in enumerate(results)]


def test_score_keeps_consistent_winners_only():
    good = flows("good.near", [1.0, 0.8, 0.5, -0.3, 0.6, 0.9])
    lucky = flows("lucky.near", [20.0, -0.9, -0.9, -0.9, -0.9, 0.1, 0.1, 0.1])  # one lucky hit is the whole profit
    small = flows("small.near", [1.0, 1.0])  # too few tokens
    loser = flows("loser.near", [-0.5] * 8)
    stats = score([*good, *lucky, *small, *loser], {})
    assert [s.wallet for s in stats] == ["good.near"] and stats[0].rank == 1
    assert stats[0].tokens == 6 >= MIN_TOKENS and stats[0].wins == 5



