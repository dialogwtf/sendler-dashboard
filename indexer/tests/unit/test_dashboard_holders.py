"""Sendler Dash: holders of launches from NEP-141 events (services/dashboard/holders.py) and the worker."""
import asyncio
import datetime as dt
import json

import pytest

from services.dashboard.holders import HolderBook, corrections, ft_deltas, stats_op
from services.dashboard.worker import DashWorker

TOK = "frog.nearlytrade.near"
DCL = "dclv2.ref-labs.near"


def ev(event, data):
    return "EVENT_JSON:" + json.dumps({"standard": "nep141", "version": "1.0.0", "event": event, "data": data})


def block(height, *receipts, ok=True):
    reos = [{"tx_hash": f"T{i}", "receipt": {"receipt_id": f"R{height}{i}", "receiver_id": ex},
             "execution_outcome": {"outcome": {"executor_id": ex, "logs": logs,
                                               "status": {"SuccessValue": ""} if ok else {"Failure": {}}}}}
            for i, (ex, logs) in enumerate(receipts)]
    return {"block": {"header": {"height": height, "prev_height": height - 1, "timestamp": 1_000_000 * 10 ** 9}},
            "shards": [{"receipt_execution_outcomes": reos}]}


def test_deltas_from_mint_transfer_burn_only_of_tracked_tokens():
    b = block(1, (TOK, [ev("ft_mint", [{"owner_id": "lock_1.nearlytrade.near", "amount": "1000"}]),
                        ev("ft_transfer", [{"old_owner_id": "lock_1.nearlytrade.near", "new_owner_id": DCL,
                                            "amount": "1000"}]),
                        ev("ft_transfer", [{"old_owner_id": DCL, "new_owner_id": "a.near", "amount": "90"},
                                           {"old_owner_id": DCL, "new_owner_id": TOK, "amount": "10",
                                            "memo": "tax"}]),
                        ev("ft_burn", {"owner_id": "a.near", "amount": "5"}),
                        ev("ft_transfer", [{"old_owner_id": "a.near", "new_owner_id": "a.near", "amount": "1"}]),
                        "not an event"]),
              ("other.near", [ev("ft_mint", [{"owner_id": "x.near", "amount": "7"}])]))
    got = [(d.account, d.amount, d.mint) for d in ft_deltas(b, {TOK})]
    assert got == [("lock_1.nearlytrade.near", 1000, True), ("lock_1.nearlytrade.near", -1000, False),
                   (DCL, 1000, False), (DCL, -90, False), ("a.near", 90, False), (DCL, -10, False),
                   (TOK, 10, False), ("a.near", -5, False)]
    failed = block(2, (TOK, [ev("ft_mint", [{"owner_id": "b.near", "amount": "1"}])]), ok=False)
    assert ft_deltas(failed, {TOK}) == []  # the logs of a reverted receipt did not change balances


def test_book_sums_between_flushes_and_remembers_first_event():
    book = HolderBook()
    book.track(TOK)
    book.add(block(1, (TOK, [ev("ft_mint", [{"owner_id": "m.near", "amount": "100"}])])))
    book.add(block(2, (TOK, [ev("ft_transfer", [{"old_owner_id": "m.near", "new_owner_id": "a.near",
                                                 "amount": "100"}])])))
    ops = book.drain(dt.datetime(2026, 11, 11, tzinfo=dt.timezone.utc))
    incs = {op._filter["_id"]: str(op._doc["$inc"]["bal"]) for op in ops}
    assert incs == {f"{TOK}:a.near": "100"}  # m.near: +100 −100 = 0 - nothing to write
    dirty, first = book.take_dirty()
    assert dirty == {TOK} and first == {TOK: True}
    assert book.take_dirty() == (set(), {})


def test_corrections_full_and_truncated_snapshot():
    stored = {"a": 5, "b": -3, "c": 7}
    assert corrections(stored, {"a": 5, "d": 2}) == {"d": 2, "b": 3, "c": -7}  # a full list: the others get zero
    top = {f"x{i}": 1 for i in range(3)}
    assert corrections(stored, top, full=False) == {"x0": 1, "x1": 1, "x2": 1, "b": 3}  # cut off: c is left alone
    assert corrections({"a": 5}, {"a": 5}) == {}  # a repeated reconciliation changes nothing


def test_worker_tracks_new_launch_token_and_writes_balances():
    from tests.unit.test_dashboard_worker import DB

    w = DashWorker(db=DB(), stream=object(), fetch=None)
    w.launches.fresh.append(TOK)  # as after parsing started
    w.height = 9
    asyncio.run(w.process(block(10, (TOK, [ev("ft_mint", [{"owner_id": "m.near", "amount": "100"}])]))))
    assert TOK in w.holders.tokens and w.launches.fresh == []

    counted = []

    async def count_documents(flt):
        counted.append(flt)
        return 1

    w.db.dash_holders.count_documents = count_documents
    asyncio.run(w._write_holders(dt.datetime(2026, 11, 11, tzinfo=dt.timezone.utc)))
    (ops,) = w.db.dash_holders.bulks
    assert [op._filter["_id"] for op in ops] == [f"{TOK}:m.near"]
    assert counted == [{"token": TOK, "bal": {"$gt": 0}}]
    (stats,) = w.db.dash_holder_stats.bulks
    assert stats[0]._filter == {"_id": TOK}


# ── a real mongod: $inc of Decimal128 and the hourly series ─────────────────

@pytest.fixture
def mongo():
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError

    client = MongoClient("mongodb://127.0.0.1:27999", serverSelectionTimeoutMS=500)
    try:
        client.admin.command("ping")
    except PyMongoError:
        pytest.skip("no local mongod on 27999")
    db = client["dash_holders_test"]
    yield db
    client.drop_database("dash_holders_test")


def test_mongo_balances_and_hourly_series(mongo):
    book = HolderBook()
    book.track(TOK)
    at = dt.datetime(2026, 11, 11, 10, 5, tzinfo=dt.timezone.utc)
    big = 10 ** 27  # u128: does not fit int64
    book.add(block(1, (TOK, [ev("ft_mint", [{"owner_id": "m.near", "amount": str(big)}]),
                             ev("ft_transfer", [{"old_owner_id": "m.near", "new_owner_id": "a.near",
                                                 "amount": str(big - 1)}])])))
    mongo.dash_holders.bulk_write(book.drain(at))
    book.add(block(2, (TOK, [ev("ft_transfer", [{"old_owner_id": "a.near", "new_owner_id": "b.near",
                                                 "amount": "1"}])])))
    mongo.dash_holders.bulk_write(book.drain(at))
    bal = {d["account"]: int(d["bal"].to_decimal()) for d in mongo.dash_holders.find()}
    assert bal == {"m.near": 1, "a.near": big - 2, "b.near": 1}
    top = [d["account"] for d in mongo.dash_holders.find({"token": TOK, "bal": {"$gt": 0}}).sort("bal", -1)]
    assert top[0] == "a.near" and mongo.dash_holders.count_documents({"token": TOK, "bal": {"$gt": 0}}) == 3

    mongo.dash_holder_stats.bulk_write([stats_op(TOK, 3, at, True)])
    mongo.dash_holder_stats.bulk_write([stats_op(TOK, 4, at + dt.timedelta(minutes=20), False)])  # the same hour
    mongo.dash_holder_stats.bulk_write([stats_op(TOK, 6, at + dt.timedelta(hours=1), None)])
    d = mongo.dash_holder_stats.find_one({"_id": TOK})
    assert d["holders"] == 6 and d["complete"] is True  # complete - from the first write, never changes afterwards
    assert [p["n"] for p in d["series"]] == [4, 6]
