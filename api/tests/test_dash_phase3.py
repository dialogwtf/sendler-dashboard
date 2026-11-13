"""/v2/dash phase 3: link previews, holders, sign-in (wallet NEP-413, Telegram via the bot), layouts, watchlist.

Data paths run on a real mongod (127.0.0.1:27999, skipped without it): Decimal128 balances, TTL and atomic
claims behave as in production there, not in a fake.
"""
import base64
import datetime as dt
import io
import os

import pytest

pytest.importorskip("httpx")

from bson.decimal128 import Decimal128
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from config import dash as cfg
from services import dash_auth as auth
from services import dash_holders, dash_og
from services import dash_service as svc
from services.dash_near import b58encode, nep413_hash

TOK = "frog.nearlytrade.near"


# ─── previews ─────────────────────────────────────────────────────────────────

def test_price_formats_like_the_site():
    assert dash_og.fmt_price(0.000000543) == "0.0₆543"
    assert dash_og.fmt_price(0.0123) == "0.0123"
    assert dash_og.fmt_price(0.00012) == "0.00012"
    assert dash_og.fmt_price(12.3456) == "12.35"
    assert dash_og.fmt_price(None) == "—"
    assert dash_og.fmt_usd(15_400) == "$15.4K" and dash_og.fmt_usd(2_100_000) == "$2.1M"
    assert dash_og.fmt_change(0.123) == "+12.3%" and dash_og.fmt_change(-0.05) == "−5.0%"


INDEX = """<!doctype html><html><head>
<meta name="description" content="site" />
<title>Sendler Dash</title>
<meta property="og:title" content="Sendler Dash — NEAR memes, live" />
<meta property="og:description" content="x" />
<meta property="og:url" content="https://dash.sendler.xyz/" />
<meta property="og:image" content="https://dash.sendler.xyz/og.png" />
<meta name="twitter:card" content="summary_large_image" />
</head><body><div id="root"></div></body></html>"""


async def test_page_rewrites_tags_and_escapes(tmp_path, monkeypatch):
    index = tmp_path / "index.html"
    index.write_text(INDEX, encoding="utf-8")
    monkeypatch.setattr(cfg, "SITE_INDEX", str(index))
    dash_og._INDEX.update(mtime=None, html=None)
    svc.CACHE.clear()

    async def token(tid):
        return {"symbol": 'FR"OG<', "name": "Frog", "price_near": 0.000000543, "price_usd": 0.0000012,
                "change_24h": 0.5, "launch": {"cap_usd": 1200.0}}

    monkeypatch.setattr(svc, "token", token)
    doc = await dash_og.page(TOK, "7")
    assert "<title>$FR&quot;OG&lt; 0.0₆543 NEAR · Sendler Dash</title>" in doc
    assert f'content="https://dash.sendler.xyz/token/{TOK}"' in doc
    assert f'content="https://api.sendler.xyz/v2/dash/og/token/{TOK}.png?v=7"' in doc
    assert 'name="twitter:image"' in doc and "+50.0% 24h" in doc and "cap $1.2K" in doc
    assert doc.count('property="og:title"') == 1 and '<div id="root">' in doc
    monkeypatch.setattr(cfg, "SITE_INDEX", str(tmp_path / "missing.html"))
    dash_og._INDEX.update(mtime=None, html=None)
    assert await dash_og.page(TOK, "7") is None  # no build - the web server serves the static index.html


def _png_uri(color) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def test_render_card_with_and_without_trades():
    full = {"symbol": "FROG", "name": "Frog on NEAR", "icon": _png_uri("green"), "price_near": 0.000000543,
            "price_usd": 0.0000012, "change_24h": -0.2, "volume_24h_usd": 5300.0,
            "launch": {"cap_usd": 1200.0, "pad": "nearly"}}
    png = dash_og.render(TOK, full, [1e-9, 2e-9, 1.5e-9], 321)
    img = Image.open(io.BytesIO(png))
    assert png[:4] == b"\x89PNG" and img.size == (1200, 630)
    bare = dash_og.render("x.near", {"icon": "data:image/svg+xml;base64,PHN2Zy8+"}, [], None)
    assert Image.open(io.BytesIO(bare)).size == (1200, 630)


def test_preview_routes_reject_junk_ids():
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/v2/dash/page/token/BAD%20ID").status_code == 404
    assert c.get("/v2/dash/og/token/" + "a" * 70 + ".png").status_code == 404


# ─── personal routes without a session ───────────────────────────────────────

def test_personal_routes_need_a_session_and_are_not_cached():
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/v2/dash/me").json() == {"detail": "unauthorized"}
    assert c.get("/v2/dash/me/layout", headers={"Authorization": "Bearer nope"}).status_code in (401, 500)
    assert c.get("/v2/dash/layouts/BAD").status_code == 404
    r = c.post("/v2/dash/auth/wallet", json={"account_id": "a.near"})
    assert r.status_code == 400 and r.json() == {"detail": "bad_request"}


# ─── real mongod ──────────────────────────────────────────────────────────────

@pytest.fixture
async def mdb(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    from pymongo.errors import PyMongoError

    client = AsyncIOMotorClient("mongodb://127.0.0.1:27999", serverSelectionTimeoutMS=500, tz_aware=True)
    try:
        await client.admin.command("ping")
    except PyMongoError:
        pytest.skip("no local mongod on 27999")
    db = client["dash_phase3_test"]
    monkeypatch.setattr(svc, "_db", lambda: db)
    monkeypatch.setattr(auth, "_db", lambda: db)
    svc.CACHE.clear()
    svc._META.clear()
    yield db
    await client.drop_database("dash_phase3_test")
    client.close()


def _h(acc, bal):
    return {"_id": f"{TOK}:{acc}", "token": TOK, "account": acc, "bal": Decimal128(str(bal))}


async def test_holders_shares_tags_and_states(mdb):
    assert (await dash_holders.holders(TOK, 10))["reason"] == "not_tracked"
    await mdb.dash_launches.insert_one({"_id": TOK, "creator": "dev.near", "pad": "nearly"})
    await mdb.dash_holder_stats.insert_one({"_id": TOK, "holders": 5, "complete": False})
    assert (await dash_holders.holders(TOK, 10))["reason"] == "pending"  # partial balances are not shown
    await mdb.dash_holder_stats.update_one({"_id": TOK}, {"$set": {"complete": True, "series": [
        {"t": dt.datetime(2026, 11, 11, 10, tzinfo=dt.timezone.utc), "n": 4}]}})
    big = 10 ** 27
    await mdb.dash_holders.insert_many([
        _h("dclv2.ref-labs.near", big // 2), _h(TOK, big // 10), _h("whale.near", big // 5), _h("dev.near", big // 10),
        _h("pal.near", big // 10), _h("gone.near", 0), _h("short.near", -5)])
    await mdb.ft_metadata.insert_one({"_id": TOK, "symbol": "FROG", "decimals": 18})
    await mdb.dash_labels.insert_one({"_id": "dclv2.ref-labs.near", "label": "Rhea DCL", "kind": "dex"})
    await mdb.sniper_dev_links.insert_one({"_id": "pal.near", "links": [{"root": "dev.near", "hop": 1}]})
    r = await dash_holders.holders(TOK, 3)
    assert r["available"] and r["complete"] and r["supply"] == str(big) and r["holders"] == 5
    assert [x["account"] for x in r["rows"]] == ["dclv2.ref-labs.near", "whale.near", TOK]
    assert r["rows"][0]["tags"] == ["pool"] and r["rows"][2]["tags"] == ["contract"]
    assert r["rows"][1]["amount"] == pytest.approx(2e8) and r["rows"][1]["share"] == pytest.approx(0.2)
    assert r["dev_share"] == pytest.approx(0.1) and r["linked_share"] == pytest.approx(0.1)  # not from the top rows
    assert r["contracts_share"] == pytest.approx(0.6)
    assert r["top10_share"] == pytest.approx(0.4)  # wallets whale + dev + pal, without the pool and the contract
    assert r["series"][0]["n"] == 4


def _wallet(nacl_signing):
    key = nacl_signing.SigningKey.generate()
    return key, "ed25519:" + b58encode(bytes(key.verify_key))


async def test_wallet_sign_in_nonce_is_one_time(mdb, monkeypatch):
    signing = pytest.importorskip("nacl.signing")

    async def owned(*a):
        return None

    monkeypatch.setattr(auth, "_key_owned", owned)
    key, pk = _wallet(signing)
    ch = await auth.challenge()
    sig = key.sign(nep413_hash(ch["message"], base64.b64decode(ch["nonce"]), ch["recipient"])).signature
    body = {"account_id": "alice.near", "public_key": pk, "signature": base64.b64encode(sig).decode(),
            "nonce": ch["nonce"], "message": ch["message"], "recipient": ch["recipient"]}
    res = await auth.wallet_login(body)
    sess = await auth.session(res["token"])
    assert sess["account"] == "alice.near" and res["me"]["account"] == "alice.near" and res["me"]["telegram"] is None
    assert len(sess["_id"]) == 64 and res["token"] not in str(await mdb[cfg.SESSIONS].find_one())  # the hash only
    with pytest.raises(auth.AuthError) as e:
        await auth.wallet_login(body)  # the same nonce again
    assert e.value.code == "bad_nonce"
    ch2 = await auth.challenge()
    with pytest.raises(auth.AuthError) as e:
        await auth.wallet_login({**body, "nonce": ch2["nonce"]})  # a signature for another nonce
    assert e.value.code == "bad_signature"
    await auth.logout(sess)
    assert await auth.session(res["token"]) is None


async def _bot_confirms(db, nonce, ref="abc234", tg_id=42):
    """What the Sendler trade bot writes."""
    await db[cfg.LOGINS].update_one({"_id": nonce, "status": "pending"}, {"$set": {
        "status": "confirmed", "tg_id": tg_id, "tg_username": "alice", "tg_name": "A", "ref_code": ref,
        "bot_user": True}})


async def test_telegram_sign_in_needs_secret_and_gives_token_once(mdb):
    st = await auth.tg_start(None)
    assert st["url"] == f"https://t.me/{cfg.BOT_USERNAME}?start=dl_{st['nonce']}"
    assert len("dl_" + st["nonce"]) <= 64
    assert (await auth.tg_poll(st["nonce"], st["secret"]))["status"] == "pending"
    await _bot_confirms(mdb, st["nonce"])
    with pytest.raises(auth.AuthError):
        await auth.tg_poll(st["nonce"], "wrong")  # the nonce is visible in the link - no session without the secret
    got = await auth.tg_poll(st["nonce"], st["secret"])
    assert got["status"] == "confirmed" and got["me"]["telegram"]["ref_code"] == "abc234"
    assert (await auth.tg_poll(st["nonce"], st["secret"]))["status"] == "used"
    sess = await auth.session(got["token"])
    assert auth.owner_of(sess) == "tg:42"
    await auth.save(sess, watchlist=[TOK, TOK, "wrap.near"])
    assert (await auth.personal(sess))["watchlist"] == [TOK, "wrap.near"]


async def test_telegram_attached_to_wallet_session_survives_next_wallet_sign_in(mdb, monkeypatch):
    signing = pytest.importorskip("nacl.signing")

    async def owned(*a):
        return None

    monkeypatch.setattr(auth, "_key_owned", owned)
    key, pk = _wallet(signing)

    async def login():
        ch = await auth.challenge()
        sig = key.sign(nep413_hash(ch["message"], base64.b64decode(ch["nonce"]), ch["recipient"])).signature
        return await auth.wallet_login({"account_id": "bob.near", "public_key": pk, "nonce": ch["nonce"],
                                        "signature": base64.b64encode(sig).decode()})

    first = await login()
    wallet_sess = await auth.session(first["token"])
    st = await auth.tg_start(wallet_sess)
    await _bot_confirms(mdb, st["nonce"], ref="ref777")
    got = await auth.tg_poll(st["nonce"], st["secret"])
    assert got["token"] is None and got["me"]["account"] == "bob.near" and got["me"]["telegram"]["ref_code"] == "ref777"
    second = await login()  # another browser: the referral code is already with the wallet
    assert second["me"]["telegram"]["ref_code"] == "ref777"
    detached = await auth.tg_detach(await auth.session(second["token"]))
    assert detached["telegram"] is None
    assert (await login())["me"]["telegram"] is None


async def test_layouts_save_share_and_limits(mdb):
    sess = {"_id": "s", "account": "carol.near"}
    saved = await auth.save(sess, layout={"widgets": [{"type": "launches", "x": 0}]})
    assert saved["layout"]["widgets"][0]["type"] == "launches" and saved["watchlist"] == []
    with pytest.raises(auth.AuthError) as e:
        await auth.save(sess, layout={"x": "a" * (cfg.LAYOUT_MAX_BYTES + 1)})
    assert e.value.code == "layout_too_big"
    with pytest.raises(auth.AuthError):
        await auth.save(sess, watchlist=["not a token!"])
    shared = await auth.share({"widgets": []})
    assert shared["url"].endswith(f"?layout={shared['code']}")
    assert (await auth.shared(shared["code"]))["layout"] == {"widgets": []}
    with pytest.raises(auth.AuthError):
        await auth.shared("zzzzzzzz")


async def test_quotes_price_change_and_cap(mdb):
    now = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    await mdb.ft_metadata.insert_one({"_id": TOK, "symbol": "FROG", "decimals": 18})
    await mdb.dash_launches.insert_one({"_id": TOK, "pad": "nearly"})
    await mdb.ft_prices.insert_one({"_id": "wrap.near", "price_usd": 2.0})
    await mdb.dash_candles.insert_many([
        {"token": TOK, "i": "1h", "t": now - dt.timedelta(hours=2), "o": 1e-27, "c": 1.5e-27, "v": 3.0},
        {"token": TOK, "i": "1h", "t": now, "o": 1.5e-27, "c": 2e-27, "v": 1.0}])
    q = (await svc.quotes([TOK, "bad id", "nothing.near"]))
    assert set(q) == {TOK, "nothing.near"}
    row = q[TOK]
    assert row["price_near"] == pytest.approx(2e-9) and row["change_24h"] == pytest.approx(1.0)
    assert row["volume_24h_near"] == pytest.approx(4.0) and row["cap_near"] == pytest.approx(2.0)
    assert row["cap_usd"] == pytest.approx(4.0) and q["nothing.near"]["price_near"] is None


def test_preview_routes_answer_head():
    c = TestClient(app, raise_server_exceptions=False)
    assert c.head("/v2/dash/page/token/BAD%20ID").status_code == 404  # not 405: HEAD is allowed
    assert c.head("/v2/dash/og/token/" + "a" * 70 + ".png").status_code == 404
