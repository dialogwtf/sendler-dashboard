"""/v2/dash — Sendler Dash: cache, fresh data for everyone, public access, CORS, open WebSocket."""
import asyncio
import datetime as dt
import json

import pytest

pytest.importorskip("httpx")

from bson import ObjectId
from fastapi.testclient import TestClient

from app.main import app
from config import dash as cfg
from services import dash_service as svc
from services.dash_kit import TTLCache, jsonable
from services.dash_ws import Client, Hub
from tests.fake_mongo import FakeDB


class AttrDB(FakeDB):
    """Motor allows db.coll as well as db["coll"]."""

    def __getattr__(self, name):
        return self[name]


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def db(monkeypatch):
    d = AttrDB()
    monkeypatch.setattr(svc, "_db", lambda: d)
    svc.CACHE.clear()
    svc._META.clear()
    yield d
    svc.CACHE.clear()
    svc._META.clear()


def _now():
    return dt.datetime.now(dt.timezone.utc)


# ─── helpers ──────────────────────────────────────────────────────────────────

def test_ttl_cache_single_flight_expiry_and_errors():
    calls = []

    async def make():
        calls.append(1)
        await asyncio.sleep(0.01)
        return len(calls)

    async def boom():
        raise RuntimeError("x")

    async def go():
        c = TTLCache()
        a, b = await asyncio.gather(c.get("k", 60, make), c.get("k", 60, make))
        again = await c.get("k", 60, make)
        fresh = await c.get("k2", 0, make)
        with pytest.raises(RuntimeError):
            await c.get("e", 60, boom)
        return a, b, again, fresh, await c.get("e", 60, make)

    assert asyncio.run(go()) == (1, 1, 1, 2, 3)


def test_jsonable_dates_ids_bytes_nan():
    oid = ObjectId()
    out = jsonable({"t": dt.datetime(2026, 11, 9, 12, 0), "id": oid, "hll": b"\x00", "x": float("nan"), "l": [1.5]})
    assert out == {"t": "2026-11-09T12:00:00Z", "id": str(oid), "x": None, "l": [1.5]}


def test_labels_and_price_helpers():
    labels = {"bank.near": {"label": "Биржа", "kind": "exchange"}}
    assert svc.label_of("bank.near", labels)["kind"] == "exchange"
    assert svc.label_of("a0b8.factory.bridge.near", {})["kind"] == "bridge"
    assert svc.label_of("f" * 64, {}) == {"label": None, "kind": "implicit"}
    assert svc.label_of("alice.near", {}) is None and svc.label_of(None, {}) is None
    assert svc.label_of("meme.launch-v2.intear.near", {}) == {"label": "Intear", "kind": "token"}
    assert svc.label_of("bob.user.intear.near", {}) is None  # an Intear wallet, not a token
    assert svc.label_of("dex-aggregator.intear.near", {}) is None


def test_launch_tax_in_percent_for_every_pad():
    assert svc.tax_pct("nearly", {"tax": {"buy_bps": 300, "sell_bps": 500, "creator_bps": 10000}}) == 5
    assert svc.tax_pct("justhoot", {"fee_pips": 20000, "shares": {"token_owner_bps": 10000}}) == 2
    assert svc.tax_pct("justhoot", {"fee_pips": 0}) == 0  # known: no tax
    assert svc.tax_pct("justhoot", {}) is None and svc.tax_pct("intear", {}) is None  # unknown
    assert svc.tax_pct("nearly", {"tax": 2}) == 2
    assert svc.human_px(2e-18, 18) == pytest.approx(2.0) and svc.human_px(None, 18) is None


# ─── WebSocket hub ────────────────────────────────────────────────────────────

def test_hub_subscribe_and_dispatch_by_channel_and_token():
    async def go():
        hub = Hub()
        a, b, c = Client("1"), Client("2"), Client("3")
        hub.clients = {a, b, c}
        assert hub.subscribe(a, "sub", ["trade"]) == {"ok": True, "subs": ["trade"]}
        hub.subscribe(b, "sub", ["trade:frog.near"])
        hub.subscribe(c, "sub", ["move"])
        assert hub.subscribe(a, "sub", ["bogus"]) == {"error": "unknown_channel", "bad": ["bogus"]}  # no ch
        assert hub.subscribe(a, "sub", [f"trade:t{i}" for i in range(cfg.WS_MAX_SUBS)])["error"] == "too_many_subs"
        doc = {"ch": "trade", "ts": dt.datetime(2026, 11, 9, tzinfo=dt.timezone.utc),
               "d": {"token": "frog.near", "near": 1}}
        hub.dispatch(doc)
        hub.dispatch({**doc, "d": {"token": "toad.near"}})
        assert (a.queue.qsize(), b.queue.qsize(), c.queue.qsize()) == (2, 1, 0)
        assert json.loads(b.queue.get_nowait()) == {"ch": "trade", "ts": "2026-11-09T00:00:00Z",
                                                    "d": {"token": "frog.near", "near": 1}}
        a.queue = asyncio.Queue(1)
        hub.dispatch(doc)
        hub.dispatch(doc)  # full queue: the event is dropped for this client, no exception
        assert a.queue.qsize() == 1

    asyncio.run(go())


def test_ws_open_without_token_subscribe_roundtrip(client, db, monkeypatch):
    monkeypatch.setattr(Hub, "join", lambda self, c: self.clients.add(c))  # no tail cursor in tests
    with client.websocket_connect("/v2/dash/ws") as ws:
        ws.send_text(json.dumps({"op": "sub", "ch": ["launch", "trade:frog.near"]}))
        assert ws.receive_json() == {"ok": True, "subs": ["launch", "trade:frog.near"]}
        ws.send_text("not json")
        assert ws.receive_json() == {"error": "bad_json"}


def test_ws_refused_when_full_or_too_many_per_ip(client, db, monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    from services.dash_ws import HUB

    monkeypatch.setattr(HUB, "clients", {Client("10.0.0.1")})
    monkeypatch.setattr(cfg, "WS_MAX_CLIENTS", 1)
    with client.websocket_connect("/v2/dash/ws") as ws:
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
    assert e.value.code == 4503

    monkeypatch.setattr(cfg, "WS_MAX_CLIENTS", 100)
    monkeypatch.setattr(cfg, "WS_PER_IP", 1)
    monkeypatch.setattr(HUB, "clients", {Client("testclient")})  # TestClient's peer address
    with client.websocket_connect("/v2/dash/ws") as ws:
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
    assert e.value.code == 4429


# ─── REST ─────────────────────────────────────────────────────────────────────

async def _seed_moves(db):
    await db.dash_moves.insert_one({"_id": "old", "ts": _now() - dt.timedelta(minutes=10), "usd": 50_000,
                                    "token": "near", "kind": "transfer", "from": "a0b8.factory.bridge.near",
                                    "to": "alice.near", "expires_at": _now()})
    await db.dash_moves.insert_one({"_id": "fresh", "ts": _now(), "usd": 20_000, "token": "near",
                                    "kind": "transfer", "from": "bob.near", "to": "alice.near"})
    await db.dash_moves.insert_one({"_id": "small", "ts": _now() - dt.timedelta(minutes=10), "usd": 500,
                                    "token": "near", "kind": "transfer", "from": "bob.near", "to": "alice.near"})


def test_moves_fresh_for_everyone_and_filtered(client, db):
    asyncio.run(_seed_moves(db))
    r = client.get("/v2/dash/moves")
    assert r.status_code == 200
    body = r.json()
    assert [m["_id"] for m in body["data"]] == ["fresh", "old"] and "delayed_s" not in body  # no guest delay
    assert body["data"][1]["from_label"]["kind"] == "bridge" and "expires_at" not in body["data"][1]
    assert r.headers["cache-control"] == "public, max-age=2"
    low = client.get("/v2/dash/moves", params={"min_usd": 100}).json()["data"]
    assert low[0]["_id"] == "fresh" and {m["_id"] for m in low} == {"fresh", "old", "small"}


def test_launches_cap_growth_and_creator_facts(client, db, monkeypatch):
    async def facts(creators):
        return {c: {"launches": 3, "dumped": 2, "blocked_by": 1, "linked": True} for c in creators}

    monkeypatch.setattr(svc, "creator_facts", facts)

    async def seed():
        await db.ft_metadata.insert_one({"_id": "frog.nearlytrade.near", "symbol": "FROG", "decimals": 18})
        await db.ft_prices.insert_one({"_id": "wrap.near", "price_usd": 2.0})
        await db.dash_launches.insert_one({"_id": "frog.nearlytrade.near", "pad": "nearly", "creator": "dev.near",
                                           "stage_n": 5, "t_first": _now(), "px0": 1e-27, "px": 3e-27,
                                           "px_max": 5e-27, "extra": {"dev_buy": 1.5, "tax": 2}})
    asyncio.run(seed())
    row = client.get("/v2/dash/launches").json()["data"][0]
    assert row["stage"] == "ready" and row["x"] == pytest.approx(3) and row["x_max"] == pytest.approx(5)
    assert row["cap_near"] == pytest.approx(3e-27 * 1e9 * 1e18) and row["cap_usd"] == pytest.approx(row["cap_near"] * 2)
    assert row["creator_facts"]["dumped"] == 2 and row["dev_buy"] == 1.5


def test_fresh_launch_gets_its_cap_once_ft_metadata_arrives(db, monkeypatch):
    """A launch row asked for before the indexer wrote ft_metadata: «no decimals» is cached only
    META_MISS_TTL_S, not 10 min — the cap shows up seconds after the metadata, while known tokens
    stay cached for META_TTL_S."""
    clock = [1000.0]
    monkeypatch.setattr(svc.time, "monotonic", lambda: clock[0])
    launch = {"_id": "frog.nearlytrade.near", "pad": "nearly", "stage_n": 5, "t_first": _now(), "px0": 1e-27, "px": 3e-27}

    async def go():
        await db.ft_prices.insert_one({"_id": "wrap.near", "price_usd": 2.0})
        first = (await svc.launch_rows([launch]))[0]
        await db.ft_metadata.insert_one({"_id": "frog.nearlytrade.near", "symbol": "FROG", "decimals": 18})
        clock[0] += svc.META_MISS_TTL_S - 1
        still = (await svc.launch_rows([launch]))[0]
        clock[0] += 2
        later = (await svc.launch_rows([launch]))[0]
        await db.ft_metadata.update_one({"_id": "frog.nearlytrade.near"}, {"$set": {"decimals": 6}})
        clock[0] += svc.META_TTL_S - 1
        cached = (await svc.launch_rows([launch]))[0]
        return first, still, later, cached

    first, still, later, cached = asyncio.run(go())
    assert first["cap_near"] is None and first["price_near"] is None and still["cap_near"] is None
    assert later["cap_near"] == pytest.approx(3e-27 * 1e9 * 1e18) and later["price_near"] == pytest.approx(3e-9)
    assert cached["cap_near"] == later["cap_near"]  # a known token: no re-read for 10 min


def test_bad_params_are_400_with_code(client, db):
    r = client.get("/v2/dash/network", params={"period": "5y"})
    assert r.status_code == 400 and r.json() == {"detail": "bad_period"}
    assert client.get("/v2/dash/launches", params={"before": "yesterday"}).json() == {"detail": "bad_before"}
    assert client.get("/v2/dash/tokens/x.near/candles", params={"i": "3m"}).json() == {"detail": "bad_interval"}


def test_public_without_api_key_and_cors_for_dash_domain(client, db, monkeypatch):
    monkeypatch.setattr("config.env.ALLOWED_API_KEYS", "k1")
    assert client.get("/v2/dash/network").status_code == 200
    assert client.get("/anything-else").status_code == 403  # the rest stays behind the key
    monkeypatch.setattr(cfg, "DASH_REQUIRE_API_KEY", True)
    monkeypatch.setattr("config.dash.DASH_REQUIRE_API_KEY", True)
    assert client.get("/v2/dash/network").status_code == 403

    pre = client.options("/v2/dash/network", headers={"Origin": "https://dash.sendler.xyz",
                                                       "Access-Control-Request-Method": "GET"})
    assert pre.headers.get("access-control-allow-origin") == "https://dash.sendler.xyz"


def test_token_traders_24h_and_candle_traders(client, db):
    async def seed():
        h = _now().replace(minute=0, second=0, microsecond=0)
        await db.ft_metadata.insert_one({"_id": "frog.near", "symbol": "FROG", "decimals": 0})
        for i, w in enumerate((["a.near", "b.near"], ["b.near", "c.near"])):
            await db.dash_candles.insert_one({"_id": f"frog.near:1h:{i}", "token": "frog.near", "i": "1h",
                                              "t": h - dt.timedelta(hours=i), "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0,
                                              "v": 1.0, "buys": 1, "sells": 0, "w": w, "traders": len(w)})
    asyncio.run(seed())
    assert client.get("/v2/dash/tokens/frog.near").json()["data"]["traders_24h"] == 3
    c = client.get("/v2/dash/tokens/frog.near/candles", params={"i": "1h"}).json()["data"]["candles"]
    assert [x["traders"] for x in c] == [2, 2] and "w" not in c[0]


def test_tokens_meta_search_and_bot_codes(client, db):
    async def seed():
        await db.ft_metadata.insert_one({"_id": "frog.near", "symbol": "FROG", "name": "Frog", "decimals": 18,
                                         "icon": "data:image/svg+xml;base64,AA=="})
        await db.ft_metadata.insert_one({"_id": "frogger.near", "symbol": "FROGGER", "decimals": 6})
        await db.ft_metadata.insert_one({"_id": "toad.near", "symbol": "TOAD", "decimals": 6})
        day = _now().replace(hour=0, minute=0, second=0, microsecond=0)
        await db.dash_candles.insert_one({"_id": "frogger.near:1d:0", "token": "frogger.near", "i": "1d", "t": day,
                                          "v": 50.0})
        await db.sniper_smart_wallets.insert_one({"_id": "whale.near", "rank": 3, "roi": 1.2, "pnl": 40.0})
    asyncio.run(seed())
    meta = client.get("/v2/dash/tokens/meta", params={"ids": "frog.near,nope.near"}).json()["data"]
    assert meta["frog.near"]["decimals"] == 18 and meta["frog.near"]["icon"].startswith("data:")
    assert meta["frog.near"]["bot_code"] == svc.bot_code("frog.near") and meta["nope.near"] is None
    # the code is the same as the bot's (indexer/services/dashboard/botlinks.py token_code)
    assert svc.bot_code("frog.near") == __import__("base64").b32encode(
        __import__("hashlib").sha256(b"frog.near").digest()).decode().lower()[:10]
    found = client.get("/v2/dash/search", params={"q": "frog"}).json()["data"]
    assert [t["token"] for t in found["tokens"]] == ["frog.near", "frogger.near"]  # the exact ticker comes first
    assert found["accounts"] == []
    who = client.get("/v2/dash/search", params={"q": "Whale.near"}).json()["data"]
    assert who["accounts"][0]["smart"]["rank"] == 3 and who["accounts"][0]["bot_code"] == svc.bot_code("whale.near")
    assert client.get("/v2/dash/search", params={"q": "f"}).json()["data"] == {"tokens": [], "accounts": []}


def test_moves_decimals_before_and_near_usd(client, db):
    asyncio.run(_seed_moves(db))
    asyncio.run(db.ft_prices.insert_one({"_id": "wrap.near", "price_usd": 2.5}))
    rows = client.get("/v2/dash/moves", params={"min_usd": 100}).json()["data"]
    assert all(r["decimals"] == 24 for r in rows)
    cut = rows[0]["ts"]
    older = client.get("/v2/dash/moves", params={"min_usd": 100, "before": cut}).json()["data"]
    assert "fresh" not in {r["_id"] for r in older} and {r["_id"] for r in older} == {"old", "small"}
    assert client.get("/v2/dash/moves", params={"before": "x"}).json() == {"detail": "bad_before"}
    assert client.get("/v2/dash/health").json()["data"]["near_usd"] == 2.5


def test_launches_hide_creators_in_shared_auto_block(client, db, monkeypatch):
    async def facts(creators):
        return {c: {"shared_block": c in await svc.shared_blocked()} for c in creators}

    monkeypatch.setattr(svc, "creator_facts", facts)  # fake mongo has no aggregate

    async def seed():
        for i, creator in enumerate(("dumper.near", "voted.near", "once.near", "clean.near")):
            await db.dash_launches.insert_one({"_id": f"t{i}.nearlytrade.near", "pad": "nearly", "creator": creator,
                                               "stage_n": 5, "t_first": _now() - dt.timedelta(minutes=i)})
        rows = [("dumper.near", 1, 2.5, None), ("voted.near", 1, 0.1, None), ("voted.near", 2, 0.2, None),
                ("once.near", 1, None, "t2.nearlytrade.near"), ("clean.near", 1, 5.0, None)]
        for i, (acc, user, sold, token) in enumerate(rows):
            await db.sniper_devs_blocked.insert_one({"_id": f"b{i}", "account": acc, "user": user, "source": "auto",
                                                     "sold_near": sold, "token": token,
                                                     **({"allowed": True} if acc == "clean.near" else {})})
        await db.sniper_launch_stats.insert_one({"_id": "t2.nearlytrade.near", "dev_sold_near": 0.3})
    asyncio.run(seed())
    every = client.get("/v2/dash/launches").json()["data"]
    assert len(every) == 4 and every[0]["creator_facts"]["shared_block"] is True
    shown = client.get("/v2/dash/launches", params={"hide_blocked": "true"}).json()["data"]
    # dumper - sold for 2.5 NEAR, voted - blocked by two users; once - 0.3 NEAR by one; clean - unblocked
    assert {r["creator"] for r in shown} == {"once.near", "clean.near"}


def test_flags_iso_times_and_language_free_labels(client, db, monkeypatch):
    async def facts(creators):
        return {}

    monkeypatch.setattr(svc, "creator_facts", facts)

    async def seed():
        await db.dash_launches.insert_one({"_id": "a.nearlytrade.near", "pad": "nearly", "creator": "dev.near",
                                           "stage_n": 5, "t_first": _now(), "fee_farm": "нет dev buy, весь налог"})
        await db.dash_launches.insert_one({"_id": "b.nearlytrade.near", "pad": "nearly", "creator": "dev.near",
                                           "stage_n": 5, "t_first": _now() - dt.timedelta(minutes=1)})
        await db.dash_labels.insert_one({"_id": "hub.near", "label": "Крупный узел", "kind": "hub"})
        await db.dash_labels.insert_one({"_id": "binance.near", "label": "Binance", "kind": "exchange"})
    asyncio.run(seed())
    rows = client.get("/v2/dash/launches").json()["data"]
    assert [r["fee_farm"] for r in rows] == [True, False]  # a flag, not the stored text
    labels = client.get("/v2/dash/labels", params={"accounts": "hub.near,binance.near,x.factory.bridge.near,"
                                                                 "y.nearlytrade.near"}).json()["data"]
    assert labels["hub.near"] == {"label": None, "kind": "hub"} and labels["binance.near"]["label"] == "Binance"
    assert labels["x.factory.bridge.near"] == {"label": "Rainbow Bridge", "kind": "bridge"}
    assert labels["y.nearlytrade.near"] == {"label": "Nearly", "kind": "token"}
    assert not any("а" <= ch <= "я" for v in labels.values() if v and v["label"] for ch in v["label"].lower())


def test_creator_history_times_are_iso(monkeypatch):
    class Cur(list):
        def sort(self, *a, **k):
            return self

        def limit(self, n):
            return self

        def __aiter__(self):
            async def gen():
                for x in list.__iter__(self):
                    yield x
            return gen()

    class Col:
        def __init__(self, docs=(), one=None):
            self.docs, self.one = list(docs), one

        def find(self, *a, **k):
            return Cur(self.docs)

        async def find_one(self, *a, **k):
            return self.one

        async def count_documents(self, *a, **k):
            return 0

    stats = [{"_id": "t.nearlytrade.near", "creator": "dev.near", "launchpad": "nearly", "t0": 1794200000.5,
              "dumped": True, "dev_in": "10", "dev_sold": "8"}]
    db = {"sniper_launch_stats": Col(stats), "dash_launches": Col(), "sniper_devs_blocked": Col(),
          "sniper_dev_links": Col(one={"links": [{"root": "r.near", "peer": "r.near", "kind": "out", "hop": 1,
                                                    "near": 2.0, "at": 1794100000.0}]})}

    class DB(dict):
        def __getattr__(self, k):
            return self[k]

    monkeypatch.setattr(svc, "_db", lambda: DB(db))

    async def rows(docs):
        return []

    monkeypatch.setattr(svc, "launch_rows", rows)
    out = asyncio.run(svc.creator("dev.near"))
    assert out["history"][0]["started"] == dt.datetime.fromtimestamp(1794200000.5, dt.timezone.utc)
    assert jsonable(out)["history"][0]["started"].endswith("Z") and jsonable(out)["links"][0]["at"].endswith("Z")
    assert out["history"][0]["sold_share"] == 0.8


def test_smart_position_entry_held_and_noise(db):
    async def seed():
        rows = [
            ("a.near", 10.0, 1000.0, 0.0),     # holds everything
            ("b.near", 30.0, 2000.0, 2000.0),  # sold all — exited
            ("c.near", 0.05, 10.0, 0.0),       # under 0.1 NEAR — noise
            ("d.near", 5.0, 0.0, 300.0),       # sold tokens it never bought by a swap
        ]
        for w, n, t_in, t_out in rows:
            await db.sniper_smart_flows.insert_one({"_id": f"{w}:frog.near", "wallet": w, "token": "frog.near",
                                                    "near_in": n, "tok_in": t_in, "tok_out": t_out})
        await db.sniper_smart_flows.insert_one({"_id": "a.near:toad.near", "near_in": 9.0, "tok_in": 9.0})
    asyncio.run(seed())
    smart = ["a.near", "b.near", "c.near", "d.near", "e.near"]
    pos = asyncio.run(svc.smart_position("frog.near", smart, 0.03, 2))
    assert pos["wallets"] == 2 and pos["holding"] == 1 and pos["invested_near"] == 40.0
    assert pos["held"] == pytest.approx(1000 / 3000)
    assert pos["entry_near"] == pytest.approx(40 / 3000 * 100)  # NEAR per raw × 10^2
    assert pos["x"] == pytest.approx(0.03 / (40 / 3000))
    assert asyncio.run(svc.smart_position("frog.near", [], 0.03, 2)) is None
    assert asyncio.run(svc.smart_position("nope.near", smart, 0.03, 2)) is None
    assert asyncio.run(svc.smart_position("frog.near", ["a.near"], None, None))["x"] is None


def test_tokens_meta_miss_is_cached_briefly(client, db):
    asyncio.run(db.ft_metadata.insert_one({"_id": "frog.near", "symbol": "FROG", "decimals": 18}))
    whole = client.get("/v2/dash/tokens/meta", params={"ids": "frog.near"})
    assert whole.headers["cache-control"] == "public, max-age=300"
    fresh = client.get("/v2/dash/tokens/meta", params={"ids": "frog.near,new.nearlytrade.near"})
    assert fresh.headers["cache-control"] == "public, max-age=15"
