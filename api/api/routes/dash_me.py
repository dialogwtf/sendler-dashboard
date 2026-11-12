"""Sendler Dash — sign-in and personal data (/v2/dash/auth/*, /v2/dash/me*, /v2/dash/layouts*). Phase 3.

Never cached (Cache-Control: private, no-store). Session: `Authorization: Bearer <token>` from a sign-in response.
Flow and storage — services/dash_auth.py. Errors: {"detail": "<code>"} — bad_request, bad_nonce, bad_signature,
key_not_owned, rpc_unavailable, unauthorized, not_found, bad_layout, layout_too_big, bad_watchlist, rate_limited.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request

from api.routes.dash import DASH_PREFIX, _reply
from config import dash as cfg
from core.api_errors import MSG_INTERNAL_SERVER_ERROR
from core.limiter import limiter
from services import dash_auth as auth
from services.dash_auth import AuthError

logger = logging.getLogger(__name__)

router = APIRouter(prefix=DASH_PREFIX, tags=["Sendler Dash — personal"])

AUTH = Header(None, alias="Authorization", description="Bearer <session token>")


async def _call(coro):
    try:
        return _reply(await coro, 0, private=True)
    except AuthError as e:
        raise HTTPException(status_code=e.status, detail=e.code)
    except HTTPException:
        raise
    except Exception:
        logger.exception("dash personal route failed")
        raise HTTPException(status_code=500, detail=MSG_INTERNAL_SERVER_ERROR)


async def _optional(authorization: Optional[str]) -> Optional[dict]:
    return await auth.session(auth.bearer(authorization))


async def _required(authorization: Optional[str]) -> dict:
    sess = await _optional(authorization)
    if sess is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    return sess


# ─── sign-in ─────────────────────────────────────────────────────────────────

@router.post("/auth/wallet/challenge", summary="NEP-413 sign-in: one-time nonce, message and recipient to sign")
@limiter.limit(cfg.AUTH_LIMIT_PER_IP)
async def wallet_challenge(request: Request):
    """→ {nonce (base64, 32 bytes), message, recipient, expires_at}. The wallet signs exactly these (signMessage)."""
    return await _call(auth.challenge())


@router.post("/auth/wallet", summary="NEP-413 sign-in: verify the signature → session")
@limiter.limit(cfg.AUTH_LIMIT_PER_IP)
async def wallet_login(request: Request, body: dict = Body(...), authorization: Optional[str] = AUTH):
    """Body: {account_id, public_key, signature, nonce, message?, recipient?, callback_url?} (the wallet's
    signMessage result + the challenge nonce). Requires a FullAccess key. → {token, expires_at, me}. Sent with the
    Bearer of a Telegram-only session — that Telegram is attached to the wallet."""
    return await _call(auth.wallet_login(body, await _optional(authorization)))


@router.post("/auth/telegram", summary="Telegram sign-in through the bot: deep link + poll secret")
@limiter.limit(cfg.AUTH_LIMIT_PER_IP)
async def telegram_start(request: Request, authorization: Optional[str] = AUTH):
    """→ {nonce, secret, url, expires_at}: open `url` (t.me/<bot>?start=dl_<nonce>), then poll
    GET /auth/telegram/{nonce}?secret=…. With a Bearer — Telegram is attached to that session."""
    return await _call(auth.tg_start(await _optional(authorization)))


@router.get("/auth/telegram/{nonce}", summary="Telegram sign-in: poll (pending / confirmed / expired / used)")
async def telegram_poll(nonce: str, secret: str = Query(..., max_length=64)):
    """confirmed → {status, token, expires_at, me} once (token null when attached to an existing session)."""
    return await _call(auth.tg_poll(nonce, secret))


@router.post("/auth/logout", summary="End the session")
async def logout(authorization: Optional[str] = AUTH):
    sess = await _required(authorization)
    await auth.logout(sess)
    return _reply({"ok": True}, 0, private=True)


# ─── me ──────────────────────────────────────────────────────────────────────

@router.get("/me", summary="Who is signed in: wallet, Telegram, referral code for bot links")
async def me(authorization: Optional[str] = AUTH):
    return _reply(auth.me(await _required(authorization)), 0, private=True)


@router.delete("/me/telegram", summary="Detach Telegram (bot links lose the referral code)")
async def me_telegram_detach(authorization: Optional[str] = AUTH):
    return await _call(auth.tg_detach(await _required(authorization)))


@router.get("/me/layout", summary="Saved layout and watchlist")
async def me_layout(authorization: Optional[str] = AUTH):
    return await _call(auth.personal(await _required(authorization)))


@router.put("/me/layout", summary="Save the layout (the site's JSON, up to 64 KB)")
async def me_layout_put(body: dict = Body(...), authorization: Optional[str] = AUTH):
    """Body: {layout: …}. → {layout, watchlist, updated_at}."""
    if "layout" not in body:
        raise HTTPException(status_code=400, detail="bad_layout")
    return await _call(auth.save(await _required(authorization), layout=body["layout"]))


@router.put("/me/watchlist", summary="Save the watchlist (up to 100 token ids)")
async def me_watchlist_put(body: dict = Body(...), authorization: Optional[str] = AUTH):
    """Body: {tokens: [...]}. Prices — GET /tokens/quotes?ids=…"""
    return await _call(auth.save(await _required(authorization), watchlist=body.get("tokens")))


# ─── shared layouts ──────────────────────────────────────────────────────────

@router.post("/layouts", summary="Share a layout: short code for ?layout=<code> (no sign-in needed)")
@limiter.limit(cfg.SHARE_LIMIT_PER_IP)
async def layout_share(request: Request, body: dict = Body(...)):
    return await _call(auth.share(body.get("layout")))


@router.get("/layouts/{code}", summary="A shared layout")
async def layout_shared(code: str):
    return await _call(auth.shared(code))
