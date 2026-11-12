"""Sendler Dash — sign-in and personal data (/v2/dash/auth/*, /v2/dash/me/*, /v2/dash/layouts/*). Phase 3.

Two ways in, both end with a session token (`Authorization: Bearer <token>`, 30 days; only its SHA-256 is stored):

- wallet, NEP-413: POST /auth/wallet/challenge → a one-time 32-byte nonce (10 min, dash_auth_nonces); the wallet signs
  AUTH_MESSAGE for AUTH_RECIPIENT with it; POST /auth/wallet checks the signature and that the key is a FullAccess
  key of the account (RPC view_access_key — the only RPC call of the dashboard, once per sign-in). The nonce is burnt on first use.
- Telegram, through the Sendler trade bot: POST /auth/telegram → dash_logins {_id: nonce, pending} and the link
  t.me/<bot>?start=dl_<nonce>; the Sendler trade bot confirms it with the Telegram id
  and the user's referral code; the site polls GET /auth/telegram/{nonce} with the `secret` it got at the start
  (the nonce is visible in the link — the secret is not) and gets a session once. Started while signed in — the
  Telegram account is attached to the current session instead (wallet + Telegram).

The referral code goes into the site's bot links (t_<code>-<ref>__dash): invitees bring the user 25% of their fees.

Personal data: dash_layouts `_id` = owner (`w:<account>` when a wallet is signed in, else `tg:<id>`): `layout`
(the site's JSON, up to 64 KB), `watchlist` (token ids), `tg` (Telegram attached to a wallet — next wallet sign-in
on another device gets it back). Shared layouts: `_id` `s:<code>` — anyone with the link copies it to himself.
"""
from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import json
import secrets

from pymongo import ASCENDING, IndexModel

from config import dash as cfg
from core.database import Database
from services.dash_service import ACCOUNT_RE
from services.dash_near import KeyCheckError, check_key_owned, decode_key, ed25519_verify, is_valid_account_id, nep413_hash

CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
CODE_LEN = 8


class AuthError(Exception):
    """HTTP status + {"detail": code}."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


def _db():
    return Database.get_db("near_indexer")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _aware(t):
    return t.replace(tzinfo=dt.timezone.utc) if isinstance(t, dt.datetime) and t.tzinfo is None else t


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def ensure_indexes() -> None:
    db = _db()
    ttl = IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0, name="expires_at_ttl")
    for name in (cfg.SESSIONS, cfg.AUTH_NONCES, cfg.LOGINS):
        await db[name].create_indexes([ttl])


# ─── sessions ────────────────────────────────────────────────────────────────

def owner_of(sess: dict) -> str:
    return f"w:{sess['account']}" if sess.get("account") else f"tg:{(sess.get('tg') or {}).get('id')}"


async def session(token: str | None) -> dict | None:
    """Live session by its bearer token, or None."""
    if not token or len(token) > 100:
        return None
    sess = await _db()[cfg.SESSIONS].find_one({"_id": token_hash(token)})
    if not sess or _aware(sess.get("expires_at")) <= _now():
        return None
    return sess


def bearer(header: str | None) -> str | None:
    if header and header[:7].lower() == "bearer ":
        return header[7:].strip() or None
    return None


async def _new_session(account: str | None, tg: dict | None) -> dict:
    token = secrets.token_urlsafe(32)
    now = _now()
    doc = {"_id": token_hash(token), "account": account, "tg": tg, "created_at": now,
           "expires_at": now + dt.timedelta(days=cfg.SESSION_TTL_DAYS)}
    await _db()[cfg.SESSIONS].insert_one(doc)
    return {"token": token, "expires_at": doc["expires_at"], "me": me(doc)}


def me(sess: dict) -> dict:
    """What the site may know: the wallet, Telegram name and the referral code (no Telegram id)."""
    tg = sess.get("tg") or None
    return {"account": sess.get("account"),
            "telegram": {"username": tg.get("username"), "name": tg.get("name"), "bot_user": bool(tg.get("bot_user")),
                         "ref_code": tg.get("ref_code")} if tg else None,
            "expires_at": sess.get("expires_at")}


async def logout(sess: dict) -> None:
    await _db()[cfg.SESSIONS].delete_one({"_id": sess["_id"]})


# ─── wallet: NEP-413 ─────────────────────────────────────────────────────────

async def challenge() -> dict:
    nonce = base64.b64encode(secrets.token_bytes(32)).decode()
    expires = _now() + dt.timedelta(seconds=cfg.AUTH_NONCE_TTL_S)
    await _db()[cfg.AUTH_NONCES].insert_one({"_id": nonce, "expires_at": expires})
    return {"nonce": nonce, "message": cfg.AUTH_MESSAGE, "recipient": cfg.AUTH_RECIPIENT, "expires_at": expires}


def _field(body: dict, name: str, max_len: int, required: bool = True) -> str | None:
    v = body.get(name)
    if v is None and not required:
        return None
    if not isinstance(v, str) or not v or len(v) > max_len:
        raise AuthError(400, "bad_request")
    return v


def _signature(value: str) -> bytes | None:
    """Wallets return base64; `ed25519:<base58>` is accepted too."""
    if value.startswith("ed25519:"):
        return decode_key(value, "ed25519", 64)
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None
    return raw if len(raw) == 64 else None


async def _key_owned(account: str, public_key: str, pk: bytes) -> None:
    """FullAccess key of the account (implicit unfunded account included)."""
    try:
        await check_key_owned(account, public_key, pk)
    except KeyCheckError as e:
        raise AuthError(e.status, e.detail)


async def wallet_login(body: dict, current: dict | None = None) -> dict:
    """Body: account_id, public_key (ed25519:…), signature (base64), nonce (from the challenge), optional message /
    recipient (must equal the challenge's) and callback_url (if the wallet signed one)."""
    account = _field(body, "account_id", 64)
    public_key = _field(body, "public_key", 100)
    sig = _signature(_field(body, "signature", 200))
    nonce_b64 = _field(body, "nonce", 64)
    callback = _field(body, "callback_url", 300, required=False)
    if body.get("message", cfg.AUTH_MESSAGE) != cfg.AUTH_MESSAGE or \
            body.get("recipient", cfg.AUTH_RECIPIENT) != cfg.AUTH_RECIPIENT:
        raise AuthError(400, "bad_request")
    if not is_valid_account_id(account) or account.startswith("0x"):
        raise AuthError(400, "bad_request")
    pk = decode_key(public_key, "ed25519", 32)
    try:
        nonce = base64.b64decode(nonce_b64, validate=True)
    except (binascii.Error, ValueError):
        nonce = b""
    if not pk or not sig or len(nonce) != 32:
        raise AuthError(400, "bad_request")
    used = await _db()[cfg.AUTH_NONCES].find_one_and_delete({"_id": nonce_b64})
    if not used or _aware(used.get("expires_at")) <= _now():
        raise AuthError(401, "bad_nonce")
    if not ed25519_verify(pk, nep413_hash(cfg.AUTH_MESSAGE, nonce, cfg.AUTH_RECIPIENT, callback), sig):
        raise AuthError(401, "bad_signature")
    await _key_owned(account, public_key, pk)

    db = _db()
    owner = f"w:{account}"
    user = await db[cfg.LAYOUTS].find_one({"_id": owner}, {"tg": 1}) or {}
    tg = user.get("tg")
    if current and current.get("tg") and not current.get("account"):  # signed in through Telegram before - attach it
        tg = current["tg"]
        await _attach_tg(owner, tg, from_owner=owner_of(current))
    return await _new_session(account, tg)


async def _attach_tg(owner: str, tg: dict, from_owner: str | None = None) -> None:
    """Telegram → wallet owner's doc; the layout and watchlist of the Telegram-only owner move over if the wallet has
    none yet."""
    db = _db()
    upd = {"tg": tg, "updated_at": _now()}
    if from_owner and from_owner != owner:
        src = await db[cfg.LAYOUTS].find_one({"_id": from_owner}, {"layout": 1, "watchlist": 1}) or {}
        mine = await db[cfg.LAYOUTS].find_one({"_id": owner}, {"layout": 1, "watchlist": 1}) or {}
        for k in ("layout", "watchlist"):
            if src.get(k) and not mine.get(k):
                upd[k] = src[k]
    await db[cfg.LAYOUTS].update_one({"_id": owner}, {"$set": upd}, upsert=True)


# ─── Telegram: through the sniper bot ────────────────────────────────────────

async def tg_start(current: dict | None) -> dict:
    nonce = secrets.token_urlsafe(24)
    secret = secrets.token_urlsafe(24)
    expires = _now() + dt.timedelta(seconds=cfg.LOGIN_TTL_S)
    await _db()[cfg.LOGINS].insert_one({"_id": nonce, "status": "pending", "secret": token_hash(secret),
                                        "session": current["_id"] if current else None,
                                        "created_at": _now(), "expires_at": expires})
    return {"nonce": nonce, "secret": secret, "url": f"https://t.me/{cfg.BOT_USERNAME}?start=dl_{nonce}",
            "expires_at": expires}


async def tg_poll(nonce: str, secret: str | None) -> dict:
    db = _db()
    doc = await db[cfg.LOGINS].find_one({"_id": nonce}) if nonce and len(nonce) <= 64 else None
    if not doc or not secret or token_hash(secret) != doc.get("secret"):
        raise AuthError(404, "not_found")
    status = doc.get("status")
    if status == "pending":
        return {"status": "expired" if _aware(doc.get("expires_at")) <= _now() else "pending"}
    if status != "confirmed":
        return {"status": status or "expired"}
    claimed = await db[cfg.LOGINS].find_one_and_update({"_id": nonce, "status": "confirmed"},
                                                       {"$set": {"status": "used", "used_at": _now()}})
    if not claimed:
        return {"status": "used"}
    tg = {"id": claimed.get("tg_id"), "username": claimed.get("tg_username"), "name": claimed.get("tg_name"),
          "ref_code": claimed.get("ref_code"), "bot_user": bool(claimed.get("bot_user"))}
    sess = await db[cfg.SESSIONS].find_one({"_id": claimed["session"]}) if claimed.get("session") else None
    if sess and _aware(sess.get("expires_at")) > _now():  # the sign-in was started from a session - Telegram goes to it
        await db[cfg.SESSIONS].update_one({"_id": sess["_id"]}, {"$set": {"tg": tg}})
        sess["tg"] = tg
        if sess.get("account"):
            await _attach_tg(f"w:{sess['account']}", tg)
        return {"status": "confirmed", "token": None, "me": me(sess)}
    return {"status": "confirmed", **await _new_session(None, tg)}


async def tg_detach(sess: dict) -> dict:
    db = _db()
    await db[cfg.SESSIONS].update_one({"_id": sess["_id"]}, {"$set": {"tg": None}})
    if sess.get("account"):
        await db[cfg.LAYOUTS].update_one({"_id": f"w:{sess['account']}"}, {"$unset": {"tg": ""}})
    sess["tg"] = None
    return me(sess)


# ─── layout and watchlist ────────────────────────────────────────────────────

def _layout(value) -> dict | list:
    if not isinstance(value, (dict, list)):
        raise AuthError(400, "bad_layout")
    if len(json.dumps(value, separators=(",", ":"))) > cfg.LAYOUT_MAX_BYTES:
        raise AuthError(413, "layout_too_big")
    return value


def _watchlist(value) -> list[str]:
    if not isinstance(value, list) or len(value) > cfg.WATCHLIST_MAX:
        raise AuthError(400, "bad_watchlist")
    out: list[str] = []
    for t in value:
        if not isinstance(t, str) or not ACCOUNT_RE.match(t) or len(t) > 64:
            raise AuthError(400, "bad_watchlist")
        if t not in out:
            out.append(t)
    return out


async def personal(sess: dict) -> dict:
    doc = await _db()[cfg.LAYOUTS].find_one({"_id": owner_of(sess)}) or {}
    return {"layout": doc.get("layout"), "watchlist": doc.get("watchlist") or [], "updated_at": doc.get("updated_at")}


async def save(sess: dict, *, layout=None, watchlist=None) -> dict:
    upd: dict = {"updated_at": _now()}
    if layout is not None:
        upd["layout"] = _layout(layout)
    if watchlist is not None:
        upd["watchlist"] = _watchlist(watchlist)
    await _db()[cfg.LAYOUTS].update_one({"_id": owner_of(sess)}, {"$set": upd}, upsert=True)
    return await personal(sess)


async def share(layout) -> dict:
    layout = _layout(layout)
    for _ in range(5):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))
        res = await _db()[cfg.LAYOUTS].update_one({"_id": f"s:{code}"}, {"$setOnInsert": {
            "layout": layout, "created_at": _now()}}, upsert=True)
        if res.upserted_id is not None:
            return {"code": code, "url": f"{cfg.SITE_URL}/?layout={code}"}
    raise AuthError(503, "try_again")


async def shared(code: str) -> dict:
    if not code or len(code) != CODE_LEN or any(c not in CODE_ALPHABET for c in code):
        raise AuthError(404, "not_found")
    doc = await _db()[cfg.LAYOUTS].find_one({"_id": f"s:{code}"})
    if not doc:
        raise AuthError(404, "not_found")
    return {"code": code, "layout": doc.get("layout"), "created_at": doc.get("created_at")}
