"""Sendler Dash: history from ft_transfers, Intear pools in new items, hub labels, traders in candles."""
import asyncio
import datetime as dt

from pytest import approx

from services.dashboard.blocks import INTEAR_XYK, intear_pools
from services.dashboard.events import HUB_LABEL, sync_hub_labels
from services.dashboard.history import (
    TxLegs,
    UsdHours,
    agg_from_candles,
    launch_doc,
    leg_of,
    near_usd_quote,
    note_launch,
    settle_native,
    trade_from,
)
from services.dashboard.trades import Candles, candle_doc, candle_op
from services.dashboard.worker import DashWorker
from tests.unit.test_dashboard_phase1 import reo
from tests.unit.test_dashboard_worker import DB
from tests.unit.intear_fixture import borsh_event

N = 10 ** 24
DCL = "dclv2.ref-labs.near"
REF = "v2.ref-finance.near"
USDC = "17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1"
MEME = "meme.nearlytrade.near"


def row(token, frm, to, amount, tx="T1", ts=1_000_000.0, signer="alice.near"):
    return {"ft_contract": token, "sender_id": frm, "receiver_id": to, "amount": str(amount), "tx_hash": tx,
            "signer_id": signer, "block_height": 1, "block_timestamp": dt.datetime.fromtimestamp(ts, dt.timezone.utc)}


def legs(*rows, ts=1_000_000.0) -> dict[str, TxLegs]:
    """Legs of one transaction by wallet, as in scripts/backfill_dash.py (with settle_native)."""
    out: dict[str, TxLegs] = {}
    for r in rows:
        got = leg_of(r)
        if got is None:
            continue
        wallet, leg = got
        t = out.setdefault(wallet, TxLegs(ts, 1, r["signer_id"]))
        t.venues.add(leg.venue)
        t.legs.append(leg)
    settle_native(out)
    return out


# ── trades from ft_transfers ─────────────────────────────────────────────────

def test_buy_and_sell_from_transfer_pairs():
    buy = legs(row("wrap.near", "alice.near", DCL, 2 * N), row(MEME, DCL, "alice.near", 1000))
    t = trade_from("alice.near", "T1", buy["alice.near"])
    assert (t.side, t.token, t.near, t.tok, t.px, t.venue, t.signer) == ("buy", MEME, 2.0, 1000, 0.002, "Rhea DCL",
                                                                         "alice.near")
    assert t.key == "bf:T1:alice.near"
    sell = legs(row(MEME, "bob.near", REF, 500, signer="bob.near"), row("wrap.near", REF, "bob.near", N))
    t = trade_from("bob.near", "T1", sell["bob.near"])
    assert (t.side, t.near, t.venue) == ("sell", 1.0, "Rhea")


def test_route_through_usdc_nets_out_and_unpaired_legs_are_not_trades():
    route = legs(row("wrap.near", "a.near", DCL, N), row(USDC, DCL, "a.near", 3_000_000),
                 row(USDC, "a.near", DCL, 3_000_000), row(MEME, DCL, "a.near", 70))
    assert trade_from("a.near", "T1", route["a.near"]).tok == 70
    order = legs(row(MEME, "a.near", DCL, 70))  # a limit order / liquidity - a single leg
    assert trade_from("a.near", "T1", order["a.near"]) is None
    arb = legs(row("wrap.near", "a.near", DCL, N), row(MEME, DCL, "a.near", 70), row(MEME, "a.near", REF, 70),
               row("wrap.near", REF, "a.near", N + 10 ** 21))
    assert trade_from("a.near", "T1", arb["a.near"]) is None  # round-trip arbitrage is not a trade
    assert leg_of(row(MEME, "a.near", "b.near", 5)) is None  # a transfer that bypasses the venues


def test_native_near_counts_only_when_venue_unwrapped_it():
    # a sale on the DCL paid out in NEAR: the DCL unwraps wNEAR (wrap.near -> DCL) and sends NEAR to the wallet
    sell = legs(row(MEME, "a.near", DCL, 500), row("near", "wrap.near", DCL, 8 * N), row("near", DCL, "a.near", 8 * N - 1))
    t = trade_from("a.near", "T1", sell["a.near"])
    assert (t.side, t.near, t.tok) == ("sell", approx(8.0), 500)
    assert set(sell) == {"a.near"}  # the unwrap entry is removed
    # JustHoot v2 creates a pool: a storage deposit refund and the whole supply into the DCL - not a sale (px0 ×10⁵ below the market)
    pc = "v2-pool-creator.justhoot.near"
    pool = legs(row("near", DCL, pc, 12_560 * 10 ** 18), row(MEME, pc, DCL, 10 ** 27), row(MEME, DCL, pc, 348))
    assert trade_from(pc, "T1", pool[pc]) is None
    # a buy with registration in the DCL: the storage payment in native NEAR is not part of the price, nor is its refund
    buy = legs(row("near", "a.near", DCL, N // 10), row("wrap.near", "a.near", DCL, 2 * N),
               row(MEME, DCL, "a.near", 1000), row("near", DCL, "a.near", N // 100))
    t = trade_from("a.near", "T1", buy["a.near"])
    assert (t.side, t.near, t.tok) == ("buy", 2.0, 1000)
    # less was unwrapped than arrived: only the unwrapped part goes into the trade
    part = legs(row(MEME, "a.near", REF, 10), row("near", "wrap.near", REF, N), row("near", REF, "a.near", N),
                row("near", REF, "a.near", N // 50))
    assert trade_from("a.near", "T1", part["a.near"]).near == 1.0


def test_late_launch_price_from_own_candles():
    t0 = dt.datetime(2026, 11, 10, 14, 4)  # from the database - no timezone, it is UTC
    rows = [{"t": t0 + dt.timedelta(minutes=1), "o": 3e-9, "h": 9e-9, "c": 5e-9, "v": 2.0, "buys": 3, "sells": 1},
            {"t": t0, "o": 1e-9, "h": 4e-9, "c": 2e-9, "v": 1.5, "buys": 2, "sells": 0}]
    a = agg_from_candles(rows)
    assert (a.px0, a.px, a.px_max, a.trades, a.volume) == (1e-9, 5e-9, 9e-9, 6, 3.5)
    assert a.first == dt.datetime(2026, 11, 10, 14, 4, tzinfo=dt.timezone.utc).timestamp()
    assert agg_from_candles([]) is None


def test_near_usd_from_stable_swaps_by_hour():
    swap = legs(row("wrap.near", "a.near", DCL, 10 * N), row(USDC, DCL, "a.near", 25_000_000))
    assert near_usd_quote(swap["a.near"]) == approx(2.5)
    dust = legs(row("wrap.near", "a.near", DCL, N // 10), row(USDC, DCL, "a.near", 250_000))
    assert near_usd_quote(dust["a.near"]) is None
    h = UsdHours()
    h.add(7200 + 5, 2.0)
    h.add(7200 + 9, 2.4)
    h.add(7200 + 30, 9.0)
    h.add(18000, 3.0)
    h.build()
    assert h.at(7300) == 2.4  # the hour's median
    assert h.at(10800 + 100) == 2.4  # an empty hour - the previous one
    assert h.at(10) == 2.4 and h.at(20000) == 3.0


def test_launch_doc_from_stats_caps_and_trades():
    aggs = {}
    for ts, near, tok in ((100.0, 1, 1000), (160.0, 3, 1000), (200.0, 2, 1000)):
        t = trade_from("a.near", f"T{ts}", legs(row("wrap.near", "a.near", DCL, near * N),
                                               row(MEME, DCL, "a.near", tok), ts=ts)["a.near"])
        note_launch(aggs, t)
    stat = {"creator": "dev.near", "launchpad": "nearly", "pool": "p", "started_at": 90.0, "t0": 100.0}
    d = launch_doc(MEME, stat, {"symbol": "MEME"}, None, aggs[MEME])
    assert (d["pad"], d["creator"], d["symbol"], d["stage_n"], d["backfill"]) == ("nearly", "dev.near", "MEME", 5, True)
    assert d["t_first"].timestamp() == 90.0 and d["t_ready"].timestamp() == 100.0
    assert (d["px0"], d["px"], d["px_max"], d["trades"], d["volume"]) == (0.001, 0.002, 0.003, 3, 6.0)
    assert launch_doc(MEME, {"creator": "dev.near"}, None, None, None) is None  # no launchpad
    bare = launch_doc(MEME, None, {"creator": "dev.near", "launchpad": "justhoot", "started_at": "50.5"}, None, None)
    assert bare["stage_n"] == 1 and "px0" not in bare


# ── candles ──────────────────────────────────────────────────────────────────

def trade(near, tok, ts, wallet="a.near"):
    return trade_from(wallet, f"T{ts}{wallet}", legs(row("wrap.near", wallet, DCL, near * N, signer=wallet),
                                                    row(MEME, DCL, wallet, tok, signer=wallet), ts=ts)[wallet])


def test_candle_traders_and_history_usd():
    c = Candles()
    c.add(trade(1, 100, 3600.0), near_usd=2.0)
    c.add(trade(1, 100, 3610.0, "b.near"), near_usd=3.0)
    c.add(trade(2, 100, 3620.0), near_usd=3.0, intervals=("1h",))
    b = c.bars[(MEME, "1h", 3600)]
    assert b.wallets == {"a.near", "b.near"} and b.v_usd == approx(11.0)
    doc = candle_doc(MEME, "1h", 3600, b, dt.datetime.now(dt.timezone.utc))
    assert (doc["traders"], doc["w"], doc["v"], doc["o"], doc["c"]) == (2, ["a.near", "b.near"], 4.0, 0.01, 0.02)
    assert (MEME, "1m", 3600) in c.bars and (MEME, "1m", 3600) not in [k for k in c.bars if k[2] == 3620]
    op = candle_op(MEME, "1h", 3600, 0.01, 0.02, 0.01, 0.02, 4.0, 3, 0, {"a.near"}, None, dt.datetime.now(),
                   close=False)
    s = op._doc[0]["$set"]
    assert s["o"] == 0.01 and s["c"] == {"$ifNull": ["$c", 0.02]} and "v_usd" not in s  # history under a live candle


# ── Intear pools and hub labels ──────────────────────────────────────────────

LAUNCH_EVENT_POOL = 304


def intear_block(rid="R1"):
    r = reo("dex.intear.near", logs=[borsh_event()], rid=rid, signer="maker.near")
    return {"block": {"header": {"height": 10, "prev_height": 9, "timestamp": 1_000_000 * 10 ** 9}},
            "shards": [{"receipt_execution_outcomes": [r]}]}


def test_intear_pool_parsed_from_borsh_event():
    [p] = intear_pools(intear_block())
    assert (p.pool_id, p.kind, p.by, p.tx_hash) == (LAUNCH_EVENT_POOL, "launch_v2", "maker.near", "TXR1")
    assert "near" in p.tokens and all(not t.startswith("nep141:") for t in p.tokens)


def test_worker_new_intear_pool_only_above_known_max():
    w = DashWorker(db=DB(), stream=object())
    w.intear_max = None
    w._discover(intear_block(), 1_000_000.0)
    assert w.new == [] and w.intear_max == LAUNCH_EVENT_POOL  # the baseline, not a new item
    w.intear_max = LAUNCH_EVENT_POOL - 1
    w._discover(intear_block(), 1_000_000.0)
    [op] = w.new
    doc = op._doc["$setOnInsert"]
    assert op._filter == {"_id": f"pool:{INTEAR_XYK}#{LAUNCH_EVENT_POOL}"}
    assert (doc["venue"], doc["pool_kind"], doc["by"]) == ("Intear DEX", "launch_v2", "maker.near")
    assert w.events[-1]["d"]["id"] == f"{INTEAR_XYK}#{LAUNCH_EVENT_POOL}"
    w._discover(intear_block("R2"), 1_000_001.0)  # the same pool changed again - not a new item
    assert len(w.new) == 1


def test_intear_known_from_own_state_or_sniper_index():
    w = DashWorker(db=DB(), stream=object())
    w.db.sniper_stream_state.docs["index:intear"] = {"pools": [[1, ["near"]], [250, ["near", "x"]]]}
    assert asyncio.run(w._intear_known()) == 250
    w.db.dash_state.docs["intear"] = {"max": 300}
    assert asyncio.run(w._intear_known()) == 300


def test_main_pool_sets_the_price_thin_pool_only_adds_volume():
    from services.dashboard.launches import LaunchTracker
    from services.dashboard.trades import MainPools, Trade

    def tr(pool, near, px, ts):
        return Trade(f"{pool}{ts}", MEME, "buy", near, 1, px, ts, 1, "T", "R", "Rhea DCL", pool, "a.near", "a.near")

    pools, c = MainPools(), Candles()
    lt = LaunchTracker()
    lt.tokens[MEME] = False
    for t in (tr("deep", 100.0, 0.01, 3600.0), tr("thin", 0.1, 0.5, 3610.0), tr("deep", 50.0, 0.011, 3620.0)):
        main = pools.note(t)
        c.add(t, price=main, intervals=("1h",))
        lt.note_trade(t.token, t.px, t.ts, t.near, price=main)
    bar = c.bars[(MEME, "1h", 3600)]
    assert (bar.o, bar.h, bar.l, bar.c) == (0.01, 0.011, 0.01, 0.011)  # a swap in a thin pool did not move the price
    assert bar.v == approx(150.1) and bar.buys == 3 and pools.main(MEME) == "deep"
    c.add(tr("thin", 0.1, 0.9, 7300.0), price=pools.note(tr("thin", 0.1, 0.9, 7300.0)), intervals=("1h",))
    assert c.bars[(MEME, "1h", 7200)].o == 0.011  # a new candle opens at the main pool's last price
    ops, _ = lt.drain()
    upd = [op for op in ops if "$inc" in op._doc][0]._doc
    assert upd["$set"]["px"] == 0.011 and upd["$max"]["px_max"] == 0.011 and upd["$inc"]["trades"] == 3
    # a thin pool that became the main one by volume starts setting the price
    p2 = MainPools()
    assert p2.note(tr("a", 1.0, 1.0, 0.0)) and not p2.note(tr("b", 0.5, 2.0, 1.0)) and p2.note(tr("b", 5.0, 2.0, 2.0))


def test_bot_link_codes_written_once_per_token():
    from services.dashboard.botlinks import token_code

    w = DashWorker(db=DB(), stream=object())
    w.note_link(MEME)
    w.note_link(MEME)
    w.note_link("other.near")
    asyncio.run(w._write_links())
    [ops] = w.db.sniper_token_links.bulks
    assert {op._filter["_id"] for op in ops} == {token_code(MEME), token_code("other.near")}
    assert {op._doc["$setOnInsert"]["token"] for op in ops} == {MEME, "other.near"}
    w.note_link(MEME)  # already written - not written again
    asyncio.run(w._write_links())
    assert len(w.db.sniper_token_links.bulks) == 1


def test_hub_labels_from_sniper_links():
    db = DB()
    db.sniper_dev_links.docs.update({"hub:abc123": {"kind": "hub"}, "hub:bridge-mng.near": {"kind": "hub"}})
    assert asyncio.run(sync_hub_labels(db)) == 2
    [ops] = db.dash_labels.bulks
    assert {op._filter["_id"] for op in ops} == {"abc123", "bridge-mng.near"}
    assert ops[0]._doc["$setOnInsert"]["label"] == HUB_LABEL and ops[0]._doc["$setOnInsert"]["kind"] == "hub"


def test_history_trades_in_pairs_not_with_near_by_hourly_quote_price():
    from services.dashboard.history import QuoteHours, history_via

    prl, usdt = "prl.omft.near", "usdt.tether-token.near"
    qh, usd = QuoteHours(), UsdHours()
    qh.add(prl, 1_000_000.0, 2e-8)  # 2e-8 NEAR per raw unit of PRL
    usd.add(1_000_000.0, 2.0)  # NEAR = $2
    qh.build()
    usd.build()
    via = history_via({prl}, qh, usd, {usdt: 6})
    buy = legs(row(prl, "a.near", DCL, 10 ** 8), row(MEME, DCL, "a.near", 500))
    t = trade_from("a.near", "T1", buy["a.near"], via)
    assert (t.side, t.token, t.near, t.tok, t.via) == ("buy", MEME, approx(2.0), 500, prl)
    sell = legs(row(MEME, "a.near", REF, 50), row(usdt, REF, "a.near", 3_000_000))
    t = trade_from("a.near", "T1", sell["a.near"], via)
    assert (t.side, t.near, t.via) == ("sell", approx(1.5), usdt)
    assert trade_from("a.near", "T1", buy["a.near"]) is None  # without via - as before
    two = legs(row(prl, "a.near", DCL, 10 ** 8), row(usdt, DCL, "a.near", 5))
    assert trade_from("a.near", "T1", two["a.near"], via) is None  # two pair assets
    far = legs(row(prl, "a.near", DCL, 10 ** 8, ts=1_000_000.0 + 7 * 3600), row(MEME, DCL, "a.near", 5))
    for tl in far.values():
        tl.ts = 1_000_000.0 + 7 * 3600
    assert trade_from("a.near", "T1", far["a.near"], via) is None  # the PRL price is older than 6 h
