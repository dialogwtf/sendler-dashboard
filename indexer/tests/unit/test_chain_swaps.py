"""services/chain/swaps.py: swaps of three venues with the pool and the receipt context (samples are real receipts of 9 Nov 2026)."""
import base64
import json

from services.chain.swaps import NEAR, Leg, block_swaps, intear_pool, receipt_swaps, swap_legs

USDC = "17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1"
DCL = "dclv2.ref-labs.near"
REF = "v2.ref-finance.near"
INTEAR = "dex.intear.near"


def ev(standard, event, data) -> str:
    return "EVENT_JSON:" + json.dumps({"standard": standard, "version": "1.0.0", "event": event, "data": data})


def reo(executor, logs, *, signer, pred, calls=(), rid="R1", tx="TX1", ok=True) -> dict:
    actions = [{"FunctionCall": {"method_name": m, "args": base64.b64encode(json.dumps(a).encode()).decode(),
                                 "deposit": "1"}} for m, a in calls]
    return {"tx_hash": tx,
            "receipt": {"receipt_id": rid, "predecessor_id": pred, "receiver_id": executor,
                        "receipt": {"Action": {"signer_id": signer, "actions": actions}}},
            "execution_outcome": {"outcome": {"executor_id": executor, "logs": logs,
                                              "status": {"SuccessValue": ""} if ok else {"Failure": {}}}}}


def block(*reos, height=100, ts_ns=1_762_650_000_000_000_000) -> dict:
    return {"block": {"header": {"height": height, "timestamp": ts_ns}},
            "shards": [{"receipt_execution_outcomes": list(reos)}]}


WALLET = "edc80b8044df4af5a05486432be105a1831678aaf419fba43ffc6fe699a0986e"
DCL_POOL = f"{USDC}|wrap.near|100"
DCL_REO = reo(DCL, [ev("dcl.ref", "swap", [{
    "amount_in": "30466438999999999711804710", "amount_out": "109720034", "pool_id": DCL_POOL,
    "protocol_fee": "609328779999999994236", "swapper": WALLET, "token_in": "wrap.near", "token_out": USDC}])],
    signer=WALLET, pred="wrap.near",
    calls=[("ft_on_transfer", {"sender_id": WALLET, "amount": "30466439000000000000000000",
                               "msg": json.dumps({"SwapByStopPoint": {"pool_id": DCL_POOL, "stop_point": 401673}})})])

INTEAR_REO = reo(INTEAR, [
    "Fully authorized: true", "Executing operation 0",
    ev("inteardex", "swap", {"dex_id": "slimedragon.near/xyk",
                             "request": {"message": "VQAAAA==", "asset_in": "near", "asset_out": f"nep141:{USDC}",
                                         "amount": {"ExactIn": "2680126005809818484161899"}, "referrer": None},
                             "amount_in": "2680126005809818484161899", "amount_out": "9659611",
                             "trader": "via-trading.near", "referrer": None, "referral_fees_paid": []}),
    ev("inteardex", "dex_events_borsh", "7AEAAPBqAgAAABAAAABzbGltZWRyYWdvbi5uZWFy")],
    signer="via-trading.near", pred="via-trading.near", rid="R2")

REF_REO = reo(REF, [
    "Swapped 5000000000000000000000000 wrap.near for 1479935 zec.omft.near",
    "Exchange v2.ref-finance.near got 7705017771827116 shares, No referral fee"],
    signer="closemeat6237.near", pred="closemeat6237.near", rid="R3",
    calls=[("swap", {"actions": [{"pool_id": 6065, "token_in": "wrap.near", "token_out": "zec.omft.near",
                                  "amount_in": "5000000000000000000000000", "min_amount_out": "1465345"}]})])


def test_dcl_swap_has_pool_actor_and_receipt_context():
    [bs] = list(block_swaps(block(DCL_REO, height=77)))
    s = bs.swap
    assert (s.venue, s.pool, s.actor) == ("Rhea DCL", DCL_POOL, WALLET)
    assert (s.token_in, s.amount_in, s.token_out, s.amount_out) == (NEAR, 30466438999999999711804710, USDC, 109720034)
    assert (bs.receipt_id, bs.tx_hash, bs.signer, bs.height, bs.key) == ("R1", "TX1", WALLET, 77, "R1:0")
    assert bs.ts == 1_762_650_000.0


def test_intear_pool_comes_from_request_message_and_borsh_event_is_ignored():
    [bs] = list(block_swaps(block(INTEAR_REO)))
    assert bs.swap.pool == "slimedragon.near/xyk#85"
    assert (bs.swap.actor, bs.swap.token_in, bs.swap.token_out) == ("via-trading.near", NEAR, USDC)
    assert bs.swap.amount_out == 9659611


def test_ref_v1_pool_from_swap_actions():
    [s] = receipt_swaps(REF, REF_REO["receipt"], REF_REO["execution_outcome"]["outcome"])
    assert (s.pool, s.actor, s.token_in, s.token_out) == (f"{REF}#6065", "closemeat6237.near", NEAR, "zec.omft.near")


def test_ref_v1_route_via_ft_on_transfer_maps_pools_in_log_order():
    msg = json.dumps({"force": 0, "actions": [{"pool_id": 1, "token_in": "wrap.near", "token_out": "a.near"},
                                              {"pool_id": 2, "token_in": "a.near", "token_out": "b.near"}]})
    r = reo(REF, ["Swapped 10 wrap.near for 20 a.near", "Swapped 20 a.near for 30 b.near"],
            signer="agg-user.near", pred="wrap.near", calls=[("ft_on_transfer", {"sender_id": "me.near", "msg": msg})])
    swaps = receipt_swaps(REF, r["receipt"], r["execution_outcome"]["outcome"])
    assert [(s.pool, s.actor, s.index) for s in swaps] == [(f"{REF}#1", "me.near", 0), (f"{REF}#2", "me.near", 1)]


def test_ref_v1_pool_unknown_when_actions_do_not_match_logs():
    r = reo(REF, ["Swapped 10 wrap.near for 20 a.near", "Swapped 20 a.near for 30 b.near"],
            signer="x.near", pred="x.near", calls=[("swap", {"actions": [{"pool_id": 1}]})])
    assert [s.pool for s in receipt_swaps(REF, r["receipt"], r["execution_outcome"]["outcome"])] == [None, None]


def test_swap_legs_keeps_the_old_shape_for_wallets_and_smart_money():
    legs = swap_legs(DCL, DCL_REO["receipt"], DCL_REO["execution_outcome"]["outcome"])
    assert legs == [(WALLET, Leg(NEAR, 30466438999999999711804710, USDC, 109720034, "Rhea DCL"))]


def test_failed_foreign_and_broken_receipts_are_skipped():
    failed = reo(DCL, DCL_REO["execution_outcome"]["outcome"]["logs"], signer=WALLET, pred="wrap.near", ok=False)
    foreign = reo("wrap.near", ["Swapped 1 a for 2 b"], signer="x.near", pred="x.near")
    broken = reo(DCL, [ev("dcl.ref", "swap", [{"amount_in": "nan", "token_in": "a", "token_out": "b"}])],
                 signer="x.near", pred="x.near", rid="R9")
    assert [bs.receipt_id for bs in block_swaps(block(failed, foreign, broken, INTEAR_REO))] == ["R2"]


def test_intear_pool_edge_cases():
    assert intear_pool(None, "VQAAAA==") is None
    assert intear_pool("dex.near/xyk", "%%%") == "dex.near/xyk"
    assert intear_pool("dex.near/xyk", base64.b64encode(b"\x01\x02").decode()) == "dex.near/xyk"
    assert intear_pool("dex.near/xyk", base64.b64encode((7).to_bytes(4, "little")).decode()) == "dex.near/xyk#7"
