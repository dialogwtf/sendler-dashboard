"""Sendler Dash: transfers, network, new items, rates, launches and the worker writing all of it."""
import asyncio
import base64
import datetime as dt
import json

from pytest import approx

from services.dashboard.blocks import discoveries, net_stats, token_standards, transfers
from services.dashboard.launches import LaunchTracker
from services.dashboard.network import HyperLogLog, NetworkAgg
from services.dashboard.prices import UsdBook
from services.dashboard.worker import DashWorker
from services.dashboard.feed import LaunchEvent
from tests.unit.test_dashboard_worker import DB

N = 10 ** 24
USDC = "17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1"


def run(coro):
    return asyncio.run(coro)


def ev(standard, event, data) -> str:
    return "EVENT_JSON:" + json.dumps({"standard": standard, "event": event, "data": data})


def reo(executor, *, pred="alice.near", signer="alice.near", actions=(), logs=(), rid="R1", ok=True, ret=None):
    status = {"SuccessValue": base64.b64encode(json.dumps(ret).encode()).decode() if ret is not None else ""} \
        if ok else {"Failure": {}}
    return {"tx_hash": "TX" + rid, "receipt": {"receipt_id": rid, "predecessor_id": pred, "receiver_id": executor,
                                               "receipt": {"Action": {"signer_id": signer, "actions": list(actions)}}},
            "execution_outcome": {"outcome": {"executor_id": executor, "logs": list(logs), "status": status,
                                              "gas_burnt": 2 * 10 ** 12, "tokens_burnt": str(N // 10_000)}}}


def call(method, args):
    return {"FunctionCall": {"method_name": method, "args": base64.b64encode(json.dumps(args).encode()).decode()}}


def block(*reos, txs=(), height=100, prev=99, ts=1_762_650_000):
    return {"block": {"header": {"height": height, "prev_height": prev, "timestamp": ts * 10 ** 9}},
            "shards": [{"chunk": {"transactions": [{"transaction": {"signer_id": s}} for s in txs]},
                        "receipt_execution_outcomes": list(reos)}]}


# ── block ───────────────────────────────────────────────────────────────────

def test_transfers_native_and_nep141_skip_system_refunds_and_failures():
    b = block(
        reo("bob.near", actions=[{"Transfer": {"deposit": str(5 * N)}}], rid="A"),
        reo("bob.near", pred="system", actions=[{"Transfer": {"deposit": str(N)}}], rid="B"),
        reo(USDC, logs=[ev("nep141", "ft_transfer", [{"old_owner_id": "a.near", "new_owner_id": "b.near",
                                                       "amount": "2500000000"}])], rid="C"),
        reo("bob.near", actions=[{"Transfer": {"deposit": str(N)}}], rid="D", ok=False))
    got = [(t.token, t.amount, t.sender, t.receiver, t.key) for t in transfers(b)]
    assert got == [("near", 5 * N, "alice.near", "bob.near", "A:t0"), (USDC, 2_500_000_000, "a.near", "b.near", "C:t0")]


def test_net_stats_counts_txs_receipts_gas_and_new_accounts():
    b = block(reo("new.near", actions=["CreateAccount", {"Transfer": {"deposit": "1"}}]), reo("x.near"),
              txs=["alice.near", "bob.near"])
    s = net_stats(b)
    assert (s.txs, s.receipts, s.new_accounts, s.signers) == (2, 2, 1, ["alice.near", "bob.near"])
    assert s.gas == 4 * 10 ** 12 and s.burnt == 2 * (N // 10_000)


def test_discoveries_new_contract_dcl_and_ref_pools():
    b = block(
        reo("meme.nearlytrade.near", pred="nearlytrade.near",
            actions=["CreateAccount", {"DeployContract": {"code": "..."}}], rid="A"),
        reo("old.near", actions=[{"DeployContract": {"code": "..."}}], rid="B"),  # a redeploy - not a new item
        reo("dclv2.ref-labs.near", logs=[ev("dcl.ref", "create_pool", [{"account_id": "nearlytrade.near",
                                                                        "pool_id": "a.near|wrap.near|10000"}])], rid="C"),
        reo("v2.ref-finance.near", actions=[call("add_simple_pool", {"tokens": ["a.near", "b.near"], "fee": 30})],
            rid="D", ret=7001))
    got = [(d.kind, d.id, d.by) for d in discoveries(b)]
    assert got == [("contract", "meme.nearlytrade.near", "nearlytrade.near"),
                   ("pool", "a.near|wrap.near|10000", "nearlytrade.near"),
                   ("pool", "v2.ref-finance.near#7001", "alice.near")]
    assert token_standards(block(reo("t.near", logs=[ev("nep141", "ft_mint", [])]))) == [("t.near", "nep141")]


# ── network ─────────────────────────────────────────────────────────────────

def test_hyperloglog_estimates_within_a_few_percent_and_restores():
    h = HyperLogLog()
    for i in range(20_000):
        h.add(f"acc{i}.near")
    assert abs(h.count() - 20_000) / 20_000 < 0.05
    assert HyperLogLog(bytes(h.r)).count() == h.count()
    small = HyperLogLog()
    for i in range(50):
        small.add(f"a{i}")
    assert 45 <= small.count() <= 55


def test_network_agg_minute_hour_day_docs():
    agg = NetworkAgg()
    ts = 1_762_650_000.0
    for start in (3600, 86400):
        agg.restore("1h" if start == 3600 else "1d", int(ts // start * start), None)
    s = net_stats(block(reo("x.near"), txs=["a.near", "b.near", "a.near"]))
    agg.add(ts, s)
    agg.add(ts + 1, s)
    ops = {op._filter["_id"].split(":")[0]: op._doc for op in agg.drain(ts + 2)}
    assert set(ops) == {"1m", "1h", "1d"}
    assert ops["1m"]["$inc"]["blocks"] == 2 and ops["1m"]["$inc"]["txs"] == 6 and ops["1m"]["$set"]["signers"] == 2
    assert ops["1h"]["$set"]["signers"] == 2 and len(ops["1h"]["$set"]["hll"]) == 4096
    assert "expires_at" in ops["1m"]["$setOnInsert"] and "expires_at" not in ops["1d"]["$setOnInsert"]


# ── rates and launches ──────────────────────────────────────────────────────

def test_usd_book_prefers_fresh_trade_price_over_ref():
    db = DB()
    db.ft_prices.docs.update({"wrap.near": {"price_usd": 4.0, "decimals": 24}, "prl.near": {"price_usd": 2.0, "decimals": 18},
                              "bad.near": {"price_usd": 1.0, "decimals": None}})
    book = UsdBook(db)
    run(book.refresh(force=True))
    assert book.near_usd == approx(4.0) and book.usd("near", 3 * N) == approx(12.0)
    assert book.usd("prl.near", 10 ** 18) == approx(2.0) and book.usd("bad.near", 1) is None
    book.note_trade("prl.near", 1e-18, ts=1000.0)  # 1 NEAR per token by a trade
    assert book.usd("prl.near", 10 ** 18, now=1000.0 + 60) == approx(4.0)
    assert book.usd("prl.near", 10 ** 18, now=1000.0 + 7200) == approx(2.0)  # the trade is stale - REF again


def test_usd_book_pair_asset_price_in_near():
    db = DB()
    db.ft_prices.docs.update({"wrap.near": {"price_usd": 2.0, "decimals": 24}})
    book = UsdBook(db)
    run(book.refresh(force=True))
    usdc = "17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1"
    assert book.near_per_raw(usdc) == approx(1e-6 / 2.0)  # a stable without a rate - $1
    book.note_trade("prl.omft.near", 3e-6, ts=1000.0)
    assert book.near_per_raw("prl.omft.near", now=1000.0 + 3600) == 3e-6
    assert book.near_per_raw("prl.omft.near", now=1000.0 + 7 * 3600) is None  # stale - and not by REF
    assert book.near_per_raw("other.near") is None


def test_launch_extra_is_plain_bson_links_as_dict_and_pair_asset():
    import bson

    from services.dashboard.links import Links

    t = LaunchTracker()
    t.add(LaunchEvent("nearly", "started", "pearl.nearlytrade.near", "dev.near", "PEARL", "Pearl",
                      "pearl.nearlytrade.near|prl.omft.near|10000", 100, "TX",
                      {"dev_buy": "0", "tax": {"buy_bps": 100}, "links": Links({"twitter": "https://x.com/p"}, "about"),
                       "fee_pips": 2 ** 70}, block_ts=1_762_650_000.0))
    t.add(LaunchEvent("nearly", "started", "q.nearlytrade.near", "dev.near", "Q", "Q",
                      "q.nearlytrade.near|wrap.near|10000", 101, "TX2", {"links": Links()}, block_ts=1_762_650_000.0))
    ops, _ = t.drain()
    s = ops[0]._doc["$set"]
    assert s["extra.links"] == {"twitter": "https://x.com/p"} and s["extra.description"] == "about"
    assert s["extra.fee_pips"] == str(2 ** 70)
    bson.encode({"u": ops[0]._doc}), bson.encode({"u": ops[1]._doc})  # what used to fail the worker's write on 9-10 Nov
    assert "extra.links" not in ops[1]._doc["$set"]
    assert "prl.omft.near" in t.quotes and "near" not in t.quotes and "token.intear.near" in t.quotes
    t.load_quotes([{"_id": "z.near", "pool": "qtc.omft.near|z.near|2000"}])
    assert "qtc.omft.near" in t.quotes
    t.load([{"_id": "late.nearlytrade.near", "backfill": True}])
    t.note_trade("late.nearlytrade.near", 5e-9, 1_762_650_100.0, 1.0)  # the live stream missed it: px0 is not set
    ops, _ = t.drain()
    assert [o._doc["$set"].get("px") for o in ops] == [5e-9]


def test_launch_tracker_stages_price_and_fee_farm():
    t = LaunchTracker()
    started = LaunchEvent("nearly", "started", "frog.nearlytrade.near", "dev.near", "FROG", "Frog",
                          "frog.nearlytrade.near|wrap.near|10000", 100, "TX", {"dev_buy": "0", "tax": {
                              "buy_bps": 300, "sell_bps": 300, "creator_bps": 10000}}, block_ts=1_762_650_000.0)
    t.add(started)
    t.add(LaunchEvent("intear", "started", None, "x.near"))  # no token - wait for ready
    t.note_trade("frog.nearlytrade.near", 1e-9, 1_762_650_010.0, 2.0)
    t.note_trade("frog.nearlytrade.near", 3e-9, 1_762_650_020.0, 1.0)
    t.note_trade("other.near", 1e-9, 1_762_650_020.0, 1.0)  # not a launch
    ops, evs = t.drain()
    first = ops[0]._doc
    assert first["$max"]["stage_n"] == 1 and first["$set"]["creator"] == "dev.near"
    assert first["$set"]["extra.dev_buy"] == "0" and first["$set"]["fee_farm"]  # no dev buy, the whole tax goes to the creator
    px0 = ops[1]
    assert px0._filter == {"_id": "frog.nearlytrade.near", "px0": {"$exists": False}} and px0._doc["$set"]["px0"] == 1e-9
    last = ops[2]._doc
    assert last["$set"]["px"] == 3e-9 and last["$max"]["px_max"] == 3e-9 and last["$inc"] == {"trades": 2, "volume": 3.0}
    assert len(ops) == 3 and [e["stage"] for e in evs] == ["started"]


# ── the whole worker ───────────────────────────────────────────────────────

def test_worker_writes_moves_network_new_labels_and_events():
    w = DashWorker(db=DB(), stream=object())
    w.db.ft_prices.docs["wrap.near"] = {"price_usd": 4.0, "decimals": 24}
    run(w.prices.refresh(force=True))
    b = block(
        reo("bob.near", actions=[{"Transfer": {"deposit": str(500 * N)}}], rid="A"),  # $2000 - a move
        reo("bob.near", actions=[{"Transfer": {"deposit": str(N)}}], rid="B"),  # $4 - not
        reo("wrap.near", pred="alice.near", actions=[{"Transfer": {"deposit": str(900 * N)}}], rid="W"),  # (un)wrap
        reo("fresh.near", actions=["CreateAccount", {"DeployContract": {"code": ""}}], rid="C"),
        reo("fresh.near", logs=[ev("nep171", "nft_mint", [])], rid="D"),
        txs=["alice.near"])
    run(w.process(b))
    run(w.flush())
    db = w.db
    [moves] = db.dash_moves.bulks
    assert [op._filter["_id"] for op in moves] == ["A:t0"]
    assert moves[0]._doc["$setOnInsert"]["usd"] == approx(2000.0)
    assert len(db.dash_network.bulks[0]) == 3
    new = [op._filter["_id"] for op in db.dash_new.bulks[0]]
    assert new == ["contract:fresh.near", "contract:fresh.near"]
    assert db.dash_new.bulks[0][1]._doc == {"$set": {"token_kind": "nft"}}
    assert [op._filter["_id"] for op in db.dash_labels.bulks[0]] == ["fresh.near"]
    channels = [e["ch"] for e in db.dash_events.bulks[0]]
    assert channels.count("new") == 2 and "move" in channels and "network" in channels
    [mv] = [e for e in db.dash_events.bulks[0] if e["ch"] == "move"]
    assert mv["d"]["id"] == "A:t0" and "_id" not in mv["d"]  # a key for deduplication on the client
    assert isinstance(db.dash_events.bulks[0][0]["ts"], dt.datetime)
