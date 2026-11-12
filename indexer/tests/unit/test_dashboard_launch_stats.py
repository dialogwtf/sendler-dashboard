"""Creator statistics per launch (services/dashboard/launch_stats.py) and reference data (reference.py)."""
import asyncio
import json

from services.chain.block import DCL
from services.dashboard.feed import LaunchEvent
from services.dashboard.launch_stats import DUMP_WINDOW_S, LaunchStats
from services.dashboard.reference import ICON_MAX, clean_meta

TOK = "dog.nearlytrade.near"
DEV = "dev.near"
BUYER = "buyer.near"
T18 = 10 ** 18


class FakeCol:
    def __init__(self):
        self.docs = {}

    async def update_one(self, flt, update, upsert=False):
        self.docs.setdefault(flt["_id"], {}).update(update["$set"])


def transfer(rid, *moves):
    return {"receipt": {"receipt_id": rid, "receiver_id": TOK},
            "execution_outcome": {"outcome": {"executor_id": TOK, "status": {"SuccessValue": ""}, "logs": [
                "EVENT_JSON:" + json.dumps({"standard": "nep141", "event": "ft_transfer", "data": [
                    {"old_owner_id": a, "new_owner_id": b, "amount": str(n)} for a, b, n in moves]})]}}}


def swap(rid, swapper, amount_in, near_out, token_out="wrap.near"):
    return {"receipt": {"receipt_id": rid, "receiver_id": DCL},
            "execution_outcome": {"outcome": {"executor_id": DCL, "status": {"SuccessValue": ""}, "logs": [
                "EVENT_JSON:" + json.dumps({"standard": "dcl.ref", "event": "swap", "data": [
                    {"swapper": swapper, "token_in": TOK, "token_out": token_out, "amount_in": str(amount_in),
                     "amount_out": str(int(near_out * 1e24))}]})]}}}


def block(h, ts, *reos):
    return {"block": {"header": {"height": h, "timestamp": int(ts * 1e9)}},
            "shards": [{"receipt_execution_outcomes": list(reos)}]}


def started(ts):
    return LaunchEvent("nearly", "started", TOK, DEV, "DOG", "Dog", f"{TOK}|wrap.near|10000", 9, "TX", {}, ts)


def test_dev_buy_and_dump_are_counted_once():
    st = LaunchStats({"sniper_launch_stats": FakeCol()})
    t = 1_000_000.0

    async def go():
        st.track(started(t))
        await st.handle(block(10, t, transfer("r1", (DCL, DEV, 1000 * T18), (DCL, BUYER, 50 * T18))))
        await st.handle(block(11, t + 60, transfer("r2", (DEV, DCL, 900 * T18)), swap("r3", DEV, 900 * T18, 2.5)))
        await st.handle(block(11, t + 60, transfer("r2", (DEV, DCL, 900 * T18))))  # the stream repeated a block
        await st.handle(block(12, t + DUMP_WINDOW_S + 1))
        await st.drain()

    asyncio.run(go())
    doc = st.col.docs[TOK]
    assert (doc["creator"], doc["launchpad"], doc["t0"]) == (DEV, "nearly", t)
    assert doc["dev_in"] == str(1000 * T18) and doc["dev_sold"] == str(900 * T18) and doc["sells"] == 1
    assert doc["dev_sold_near"] == 2.5 and doc["first_sell_s"] == 60 and doc["dumped"] is True


def test_holding_creator_is_not_a_dump_and_other_pairs_have_no_near_amount():
    st = LaunchStats({"sniper_launch_stats": FakeCol()})
    t = 2_000_000.0

    async def go():
        st.track(started(t))
        await st.handle(block(10, t, transfer("r1", (DCL, DEV, 1000 * T18), (DCL, BUYER, 50 * T18))))
        await st.handle(block(11, t + 30, transfer("r2", (DEV, DCL, 100 * T18)),
                              swap("r3", DEV, 100 * T18, 9.0, token_out="prl.omft.near")))
        await st.handle(block(12, t + DUMP_WINDOW_S + 1))
        await st.drain()

    asyncio.run(go())
    doc = st.col.docs[TOK]
    assert doc["dumped"] is False and doc["dev_sold"] == str(100 * T18) and doc["dev_sold_near"] == 0


def test_token_metadata_is_untrusted_input():
    assert clean_meta({"symbol": " DOG ", "name": "Dog", "decimals": 18, "icon": "data:image/svg+xml,<svg/>"}) == {
        "symbol": "DOG", "name": "Dog", "decimals": 18, "icon": "data:image/svg+xml,<svg/>"}
    assert clean_meta({"symbol": "X" * 500, "decimals": "6", "icon": "a" * (ICON_MAX + 1)}) == {
        "symbol": "X" * 64, "name": None, "decimals": 6, "icon": None}
    assert clean_meta({"symbol": "DOG"}) is None and clean_meta({"symbol": "", "decimals": 18}) is None
    assert clean_meta({"symbol": "DOG", "decimals": 99}) is None
