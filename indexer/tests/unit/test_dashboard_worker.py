"""Sendler Dash: trades and candles from swaps (services/dashboard/trades.py) and the worker on the block stream."""
import asyncio
import json

import pytest
from pymongo.errors import DuplicateKeyError
from pytest import approx

from services.chain.swaps import BlockSwap, Swap
from services.dashboard import worker as worker_mod
from services.dashboard.trades import Candles, block_trades, trade_of
from services.dashboard.worker import DashWorker

N = 10 ** 24
MEME = "frog.nearlytrade.near"
USDC = "17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1"
DCL = "dclv2.ref-labs.near"


def bs(tin, ain, tout, aout, rid="R1", idx=0, ts=1_000_000.0, height=10) -> BlockSwap:
    return BlockSwap(Swap("Rhea DCL", "p", "w.near", tin, ain, tout, aout, idx), rid, "TX", "w.near", height, ts)


# ── trades ──────────────────────────────────────────────────────────────────

def test_trade_side_price_and_skips():
    buy = trade_of(bs("near", 2 * N, MEME, 4_000))
    assert (buy.side, buy.token, buy.near, buy.tok, buy.px) == ("buy", MEME, 2.0, 4_000, 2.0 / 4_000)
    sell = trade_of(bs(MEME, 1_000, "near", N // 2))
    assert (sell.side, sell.near, sell.px) == ("sell", 0.5, 0.5 / 1_000)
    assert trade_of(bs("near", N, USDC, 3_000_000)) is None  # a stable - not a token trade
    assert trade_of(bs(USDC, 1, MEME, 5)) is None  # not a pair against NEAR
    assert trade_of(bs("near", 0, MEME, 5)) is None


def test_pair_not_with_near_trades_through_quote_price():
    PRL = "prl.omft.near"
    px = {PRL: 2e-6, USDC: 1e-6 / 2.0}.get  # PRL: 2e-6 NEAR per raw unit; USDC: $1 at NEAR = $2
    buy = trade_of(bs(PRL, 1_000_000, MEME, 4_000), via=px)
    assert (buy.side, buy.token, buy.near, buy.tok, buy.via) == ("buy", MEME, approx(2.0), 4_000, PRL)
    assert buy.doc(7, None)["via"] == PRL
    sell = trade_of(bs(MEME, 1_000, USDC, 3_000_000), via=px)
    assert (sell.side, sell.token, sell.near, sell.via) == ("sell", MEME, approx(1.5), USDC)
    assert trade_of(bs(PRL, 5, USDC, 5), via=px) is None  # two pair assets - not a trade
    assert trade_of(bs("zec.omft.near", 5, MEME, 5), via=px) is None  # no asset price
    assert trade_of(bs("near", N, MEME, 4_000), via=px).via is None


def test_worker_prices_route_through_pair_asset_in_one_block():
    w, _ = make()
    w.launches.quotes.add("prl.omft.near")
    # NEAR -> PRL (PRL price: 1 NEAR per 1e6 raw), then PRL -> token in the same receipt / block
    run(w.process(block(100, 99, ("near", N, "prl.omft.near", 1_000_000), ("prl.omft.near", 500_000, MEME, 100))))
    meme = [t for t in w.trades if t.token == MEME]
    assert len(meme) == 1 and meme[0].near == approx(0.5) and meme[0].via == "prl.omft.near"


def test_warm_up_takes_pair_asset_price_from_own_hourly_candle():
    import datetime as dt

    w, _ = make()
    w.db.dash_launches.docs["pearl.nearlytrade.near"] = {"pool": "pearl.nearlytrade.near|prl.omft.near|10000",
                                                         "t_first": dt.datetime.now()}
    hour = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0, tzinfo=None)  # as from the database
    seen = []

    async def find_one(flt, proj=None, sort=None):
        seen.append(flt.get("token"))
        return {"c": 4e-6, "t": hour} if flt.get("token") == "prl.omft.near" else None

    w.db.dash_candles.find_one = find_one
    run(w.warm_up())
    assert "prl.omft.near" in seen and w.prices.near_per_raw("prl.omft.near") == 4e-6


def test_round_trip_route_is_arbitrage_but_both_legs_trade():
    swaps = [bs("near", N, MEME, 100, rid="A", idx=0), bs(MEME, 100, "near", N + 1, rid="A", idx=1),
             bs("near", N, MEME, 90, rid="B")]
    trades = block_trades(swaps)
    assert [(t.key, t.arb) for t in trades] == [("A:0", True), ("A:1", True), ("B:0", False)]


def test_candles_accumulate_ohlc_volume_and_ttl():
    c = Candles()
    for i, (near, tok) in enumerate([(1, 100), (3, 100), (2, 100)]):  # prices 0.01, 0.03, 0.02
        c.add(trade_of(bs("near", near * N, MEME, tok, rid=f"R{i}", ts=1_000_000.0 + i)))
    c.add(trade_of(bs(MEME, 100, "near", N, rid="S", ts=1_000_003.0)))  # a sale at 0.01
    ops = {op._filter["_id"]: op._doc[0]["$set"] for op in c.drain(near_usd=3.5)}
    assert len(ops) == 3 and len(c) == 0
    m = ops[f"{MEME}:1m:{1_000_000 // 60 * 60}"]
    assert m["o"] == {"$ifNull": ["$o", 0.01]} and m["h"]["$max"][1] == 0.03 and m["l"]["$min"][1] == 0.01
    assert m["c"] == 0.01 and m["v"]["$add"][1] == 7.0 and m["v_usd"]["$add"][1] == pytest.approx(24.5)
    assert (m["buys"]["$add"][1], m["sells"]["$add"][1]) == (3, 1)
    assert "expires_at" in m
    day = ops[f"{MEME}:1d:{1_000_000 // 86400 * 86400}"]
    assert "expires_at" not in day


# ── worker ──────────────────────────────────────────────────────────────────

class Coll:
    def __init__(self):
        self.docs = {}
        self.bulks = []
        self.dup = False

    async def update_one(self, flt, upd, upsert=False):
        if self.dup:
            raise DuplicateKeyError("lease")
        self.docs.setdefault(flt["_id"], {}).update(upd.get("$set", {}))

    async def find_one(self, flt, proj=None):
        return self.docs.get(flt["_id"])

    async def delete_one(self, flt):
        self.docs.pop(flt["_id"], None)

    async def bulk_write(self, ops, ordered=True):
        self.bulks.append(ops)

    async def insert_many(self, docs, ordered=True):
        self.bulks.append(list(docs))

    def find(self, flt=None, proj=None):
        async def gen():
            for k, d in list(self.docs.items()):
                yield {"_id": k, **d}
        return gen()

    async def create_index(self, *a, **kw):
        return None


class DB:
    def __init__(self):
        self._c = {}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self._c.setdefault(name, Coll())

    def __getitem__(self, name):
        return getattr(self, name)

    async def list_collection_names(self, filter=None):
        return list(self._c)

    async def create_collection(self, name, **kw):
        return getattr(self, name)


def ev(data) -> str:
    return "EVENT_JSON:" + json.dumps({"standard": "dcl.ref", "event": "swap", "data": data})


def block(height, prev, *swaps) -> dict:
    reos = [{"tx_hash": f"T{height}{i}",
             "receipt": {"receipt_id": f"R{height}{i}", "predecessor_id": "w.near", "receiver_id": DCL,
                         "receipt": {"Action": {"signer_id": "w.near", "actions": []}}},
             "execution_outcome": {"outcome": {"executor_id": DCL, "status": {"SuccessValue": ""},
                                               "logs": [ev([{"swapper": "w.near", "pool_id": "p", "token_in": tin,
                                                             "amount_in": str(ain), "token_out": tout,
                                                             "amount_out": str(aout)}])]}}}
            for i, (tin, ain, tout, aout) in enumerate(swaps)]
    return {"block": {"header": {"height": height, "prev_height": prev, "timestamp": 1_000_000 * 10 ** 9}},
            "shards": [{"receipt_execution_outcomes": reos}]}


def run(coro):
    return asyncio.run(coro)


def make(fetched=None):
    calls = []

    async def fetch(h):
        calls.append(h)
        return (fetched or {}).get(h)

    w = DashWorker(db=DB(), stream=object(), fetch=fetch)
    return w, calls


def test_gap_by_prev_height_is_filled_and_old_blocks_ignored():
    w, calls = make(fetched={101: block(101, 100, ("near", N, MEME, 10))})  # 102 is an empty height
    run(w.process(block(100, 99, ("near", N, MEME, 10))))
    run(w.process(block(103, 102, ("near", 2 * N, MEME, 10))))
    assert calls == [101, 102] and w.height == 103
    run(w.process(block(102, 101, ("near", 9 * N, MEME, 10))))  # a late one - already passed
    assert [t.key for t in w.trades] == ["R1000:0", "R1010:0", "R1030:0"]


def test_empty_heights_without_gap_are_not_fetched():
    w, calls = make()
    run(w.process(block(100, 99)))
    run(w.process(block(105, 100)))  # 101–104 are empty: prev_height points to 100
    assert calls == [] and w.height == 105


def test_too_long_gap_is_skipped(monkeypatch):
    monkeypatch.setattr(worker_mod.Settings.__class__, "DASH_GAP_MAX", property(lambda self: 5))
    w, calls = make()
    run(w.process(block(100, 99)))
    run(w.process(block(200, 199)))
    assert calls == [] and w.height == 200


def test_full_queue_drops_without_waiting():
    w, _ = make()

    async def go():
        w.queue = asyncio.Queue(maxsize=1)
        await w.on_block(block(1, 0))
        await w.on_block(block(2, 1))  # does not wait for room
        return w.queue.qsize()

    assert run(go()) == 1 and w.dropped == 1


def test_flush_writes_trades_candles_and_cursor():
    w, _ = make()
    w.db.ft_prices.docs["wrap.near"] = {"price_usd": 3.0, "decimals": 24}
    run(w.prices.refresh(force=True))
    run(w.process(block(100, 99, ("near", N, MEME, 10), ("near", N // 100, MEME, 1))))  # the second one - 0.01 NEAR
    run(w.flush())
    [trades] = w.db.dash_trades.bulks
    assert [op._filter["_id"] for op in trades] == ["R1000:0"]  # the small one - in candles only
    doc = trades[0]._doc["$setOnInsert"]
    assert (doc["side"], doc["near"], doc["usd"], doc["tok"]) == ("buy", 1.0, 3.0, "10")
    [candles] = w.db.dash_candles.bulks
    assert len(candles) == 3 and candles[0]._doc[0]["$set"]["buys"]["$add"][1] == 2
    assert w.db.dash_state.docs["cursor"]["height"] == 100 and w.trades == []


def test_gap_fetch_is_retried_once(monkeypatch):
    monkeypatch.setattr(worker_mod, "FETCH_RETRY_S", 0)
    tries = []

    async def flaky(h):
        tries.append(h)
        if tries.count(h) == 1:
            raise RuntimeError("neardata: HTTP 429")
        return block(h, h - 1, ("near", N, MEME, 10))

    w = DashWorker(db=DB(), stream=object(), fetch=flaky)
    run(w.process(block(100, 99)))
    run(w.process(block(102, 101)))
    assert tries == [101, 101] and [t.key for t in w.trades] == ["R1010:0"]


def test_lease_taken_by_another_holder():
    w, _ = make()
    w.db.dash_state.dup = True
    assert run(w.acquire_lease()) is False


def test_new_tokens_get_bot_link_codes_before_first_trade(monkeypatch):
    """"Buy in the bot" on the site works by the code from sniper_token_links: a new FT and a new pool's tokens get a code at once."""
    from services.dashboard.blocks import FT_STANDARD, NFT_STANDARD, Discovery, IntearPool

    w, _ = make()
    w.intear_max = 5
    monkeypatch.setattr(worker_mod, "discoveries", lambda b: [
        Discovery("contract", "fresh.near", "dev.near", "T1"),
        Discovery("contract", "art.near", "dev.near", "T2"),
        Discovery("pool", "listed.near|wrap.near|2000", "dev.near", "T3", {"venue": "Rhea DCL", "tokens": ["listed.near", "wrap.near"]}),
    ])
    monkeypatch.setattr(worker_mod, "intear_pools", lambda b: [IntearPool(6, "xyk", ("near", "pooled.near"), "T4", "dev.near")])
    monkeypatch.setattr(worker_mod, "token_standards", lambda b: [("fresh.near", FT_STANDARD), ("art.near", NFT_STANDARD)])
    w._discover({}, 1_000_000.0)
    assert w.links == {"fresh.near", "listed.near", "wrap.near", "pooled.near"}  # the NFT and Intear's "near" - no code


def test_hourly_links_cover_smart_wallets_and_every_known_token():
    """The site's search finds tokens in ft_metadata: the bot needs their codes too, even without trades against NEAR (USDT, wNEAR)."""
    w, _ = make()
    w.db.sniper_smart_wallets.docs.update({"whale.near": {"rank": 1}})
    w.db.ft_metadata.docs.update({"usdt.tether-token.near": {"symbol": "USDt"}, "wrap.near": {"symbol": "wNEAR"}})
    run(w._link_smart())
    assert w.links == {"whale.near", "usdt.tether-token.near", "wrap.near"}
    run(w._write_links())
    run(w._link_smart())
    assert w.links == set()  # written ones are not sent again


def test_warm_up_keeps_old_launches_trading_and_tracks_their_holders():
    import datetime as dt

    w, _ = make()
    old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)  # before 11 Nov only 3 days were loaded
    w.db.dash_launches.docs[MEME] = {"t_first": old, "px0": 1e-9, "pool": f"{MEME}|wrap.near|10000"}
    run(w.warm_up())
    assert MEME in w.launches.tokens and MEME in w.holders.tokens
    w.launches.note_trade(MEME, 2e-9, 1_000_000.0, 1.0)
    ops, _ = w.launches.drain()
    assert any(op._doc.get("$set", {}).get("px") == 2e-9 for op in ops)
