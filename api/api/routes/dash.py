"""Sendler Dash — /v2/dash: public dashboard dash.sendler.xyz (indexer/docs/DASH_DATA.md).

REST is a snapshot for the first paint of a widget: responses come from an in-memory cache and carry
Cache-Control for nginx. Live updates — WebSocket /v2/dash/ws, open to everyone without a token.
Everyone gets the same up-to-date data (no guest delay, no login — owner's decisions 2026-11-09).
Called from the browser without X-API-Key
(api/middleware.py). Never calls RPC. Errors: {"detail": "<code>"}.
"""
import asyncio
import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response

from config import dash as cfg
from core.api_errors import MSG_INTERNAL_SERVER_ERROR
from core.limiter import client_ip
from services import dash_holders, dash_nft, dash_og
from services import dash_service as svc
from services.dash_kit import jsonable
from services.dash_ws import HUB, Client

logger = logging.getLogger(__name__)

DASH_PREFIX = "/v2/dash"
META_MISS_TTL_S = 15
router = APIRouter(prefix=DASH_PREFIX, tags=["Sendler Dash"])


def _reply(data, ttl: float, *, private: bool = False) -> Response:
    body = {"data": jsonable(data)}
    cc = "private, no-store" if private else f"public, max-age={int(ttl)}"
    return Response(json.dumps(body, ensure_ascii=False, separators=(",", ":")),
                    media_type="application/json", headers={"Cache-Control": cc})


async def _cached(request: Request, ttl: float, make):
    """Cache by path + sorted query."""
    q = sorted(request.query_params.multi_items())
    key = request.url.path + "?" + "&".join(f"{k}={v}" for k, v in q)
    try:
        return await svc.CACHE.get(key, ttl, make)
    except svc.DashError as e:
        raise HTTPException(status_code=400, detail=e.code)
    except HTTPException:
        raise
    except Exception:
        logger.exception("dash %s failed", request.url.path)
        raise HTTPException(status_code=500, detail=MSG_INTERNAL_SERVER_ERROR)


LIMIT = Query(50, ge=1, le=cfg.MAX_LIMIT)


@router.get("/health", summary="Indexer progress of the dashboard data")
async def health():
    data = await svc.health()
    data["ws_clients"] = len(HUB.clients)
    return _reply(data, 0, private=True)


@router.get("/launches", summary="Launches Nearly / JustHoot / Intear, newest first")
async def launches(request: Request, pad: Optional[str] = None, hide_fee_farm: bool = False,
                   hide_blocked: bool = Query(False, description="hide creators in the sniper's shared auto-block"),
                   before: Optional[str] = Query(None, description="ISO time: page before this launch"),
                   limit: int = LIMIT):
    pads = [p for p in (pad or "").split(",") if p]
    return _reply(await _cached(request, 2, lambda: svc.launches(pads, hide_fee_farm, before, limit, hide_blocked)), 2)


@router.get("/launches/movers", summary="Launches growing fastest over the period (volume from $500)")
async def movers(request: Request, period: str = "10m", limit: int = Query(20, ge=1, le=50)):
    return _reply(await _cached(request, 10, lambda: svc.movers(period, limit)), 10)


@router.get("/tokens/top", summary="Top tokens by volume / growth / trades")
async def tokens_top(request: Request, period: str = "24h", sort: str = "volume", limit: int = LIMIT):
    return _reply(await _cached(request, 30, lambda: svc.tokens_top(period, sort, limit)), 30)


@router.get("/tokens/meta", summary="Symbol, decimals, icon and bot link code of tokens for list rows")
async def tokens_meta(request: Request, ids: str = Query("", description="comma-separated, up to 100")):
    tokens = [t for t in ids.split(",") if t][:100]
    # A fresh launch is listed before the indexer writes its ft_metadata: such an answer (null / no
    # decimals) is cached 15 s, not 5 min — the site asks again and gets the logo and decimals.
    data = await _cached(request, META_MISS_TTL_S, lambda: svc.tokens_meta(tokens))
    whole = all(v is not None and v.get("decimals") is not None for v in data.values())
    return _reply(data, 300 if whole else META_MISS_TTL_S)


@router.get("/tokens/quotes", summary="Watchlist rows: price, 24h change and volume, cap — up to 100 tokens")
async def tokens_quotes(request: Request, ids: str = Query("", description="comma-separated, up to 100")):
    tokens = [t for t in ids.split(",") if t][:cfg.WATCHLIST_MAX]
    return _reply(await _cached(request, 10, lambda: svc.quotes(tokens)), 10)


@router.get("/search", summary="Search tokens by symbol or contract, accounts by id")
async def search(request: Request, q: str = Query("", max_length=64), limit: int = Query(10, ge=1, le=20)):
    return _reply(await _cached(request, 30, lambda: svc.search(q, limit)), 30)


@router.get("/tokens/{token_id}", summary="Token card: price, 24h, launch, smart money")
async def token(request: Request, token_id: str):
    return _reply(await _cached(request, 5, lambda: svc.token(token_id)), 5)


@router.get("/tokens/{token_id}/candles", summary="OHLCV candles (px = NEAR per raw unit)")
async def candles(request: Request, token_id: str, i: str = "1m",
                  start: Optional[float] = Query(None, alias="from", description="unix seconds"),
                  end: Optional[float] = Query(None, alias="to", description="unix seconds"),
                  limit: int = Query(500, ge=1, le=cfg.MAX_CANDLES)):
    return _reply(await _cached(request, 5, lambda: svc.candles(token_id, i, start, end, limit)), 5)


BEFORE = Query(None, description="ISO time of the last row the client has: the page before it")


@router.get("/tokens/{token_id}/trades", summary="Latest trades, tagged smart / creator")
async def trades(request: Request, token_id: str, limit: int = LIMIT, before: Optional[str] = BEFORE):
    return _reply(await _cached(request, 2, lambda: svc.trades(token_id, limit, before)), 2)


@router.get("/tokens/{token_id}/holders", summary="Holders of a launch token: top, shares, holder count series")
async def holders(request: Request, token_id: str, limit: int = Query(50, ge=1, le=cfg.HOLDERS_MAX)):
    return _reply(await _cached(request, 30, lambda: dash_holders.holders(token_id, limit)), 30)


@router.get("/creators/{account}", summary="Creator dossier: past launches, dumps, blocks, linked wallets")
async def creator(request: Request, account: str):
    return _reply(await _cached(request, 60, lambda: svc.creator(account)), 60)


@router.get("/smart/top", summary="Smart money: top wallets for 30 days")
async def smart_top(request: Request, limit: int = LIMIT):
    return _reply(await _cached(request, 60, lambda: svc.smart_top(limit)), 60)


@router.get("/smart/feed", summary="Smart money trades")
async def smart_feed(request: Request, limit: int = LIMIT, before: Optional[str] = BEFORE):
    return _reply(await _cached(request, 5, lambda: svc.smart_feed(limit, before)), 5)


@router.get("/moves", summary="Big transfers and swaps")
async def moves(request: Request, min_usd: float = Query(10_000, ge=0), token: Optional[str] = Query(
        None, alias="token_id"), kind: Optional[str] = None, limit: int = LIMIT, before: Optional[str] = BEFORE):
    return _reply(await _cached(request, 2, lambda: svc.moves(min_usd, token, kind, limit, before)), 2)


@router.get("/network", summary="Network: txs, receipts, gas, burnt, new accounts, signers")
async def network(request: Request, period: str = "1h"):
    return _reply(await _cached(request, 10, lambda: svc.network(period)), 10)


@router.get("/new", summary="New contracts, tokens, NFT and pools")
async def new(request: Request, kind: Optional[str] = None, limit: int = LIMIT, before: Optional[str] = BEFORE):
    return _reply(await _cached(request, 10, lambda: svc.new(kind, limit, before)), 10)


@router.get("/labels", summary="Labels of accounts (exchanges, venues, bridges…)")
async def labels(request: Request, accounts: str = Query("", description="comma-separated, up to 100")):
    accs = [a for a in accounts.split(",") if a][:100]
    return _reply(await _cached(request, 300, lambda: svc.labels(accs)), 300)


@router.get("/nft/collections", summary="NFT market: top collections of sendler-market")
async def nft_collections(request: Request, sort: str = "volume_24h", limit: int = LIMIT):
    return _reply(await _cached(request, 60, lambda: dash_nft.collections(sort, limit)), 60)


@router.get("/nft/sales", summary="NFT market: latest sales (cursor = next_cursor of the previous page)")
async def nft_sales(request: Request, limit: int = LIMIT, cursor: Optional[str] = Query(None, max_length=2000)):
    return _reply(await _cached(request, 10, lambda: dash_nft.sales(limit, cursor)), 10)


@router.get("/nft/stats", summary="NFT market: totals now and per UTC day")
async def nft_stats(request: Request, days: int = Query(30, ge=1, le=90)):
    return _reply(await _cached(request, 300, lambda: dash_nft.stats(days)), 300)


def _token_or_404(token_id: str) -> str:
    if len(token_id) > 64 or not svc.ACCOUNT_RE.match(token_id):
        raise HTTPException(status_code=404, detail="not_found")
    return token_id


# HEAD too: some link-preview crawlers ask HEAD first (FastAPI does not add it to GET routes)
@router.api_route("/page/token/{token_id}", methods=["GET", "HEAD"], response_class=HTMLResponse,
                  include_in_schema=False)
async def token_page(token_id: str):
    """The site's index.html with this token's og:/twitter: tags — nginx of dash.sendler.xyz proxies /token/<id>
    here (link previews in Telegram, X…) and serves the static index.html on any error."""
    token_id = _token_or_404(token_id)
    try:
        doc = await dash_og.page(token_id, str(int(time.time() // 3600)))
    except Exception:
        logger.exception("dash og page %s failed", token_id)
        raise HTTPException(status_code=503, detail="unavailable")
    if doc is None:
        raise HTTPException(status_code=503, detail="site_index_missing")
    return HTMLResponse(doc, headers={"Cache-Control": "public, max-age=60"})


@router.api_route("/og/token/{token_id}.png", methods=["GET", "HEAD"], include_in_schema=False)
async def token_image(token_id: str):
    """Preview card 1200×630 of a token page."""
    token_id = _token_or_404(token_id)
    try:
        png = await dash_og.image(token_id)
    except Exception:
        logger.exception("dash og image %s failed", token_id)
        raise HTTPException(status_code=503, detail="unavailable")
    return Response(png, media_type="image/png", headers={"Cache-Control": "public, max-age=600"})


@router.websocket("/ws")
async def ws(websocket: WebSocket):
    """Live events, open to everyone. Messages:
    {"op": "sub"|"unsub"|"ping", "ch": ["launch", "trade", "trade:<token>", "move", "new", "network"]}.
    Events: {"ch", "ts", "d"}. Close codes: 4429 too many connections from this IP, 4503 server full."""
    # Accept first, then close with a code: a refused handshake reaches the browser only as 1006 with no reason.
    await websocket.accept()
    ip = client_ip(websocket)
    refusal = None
    if len(HUB.clients) >= cfg.WS_MAX_CLIENTS:
        refusal = (4503, "ws_full")
    elif HUB.per_ip(ip) >= cfg.WS_PER_IP:
        refusal = (4429, "ws_per_ip")
    if refusal:
        await websocket.close(code=refusal[0], reason=refusal[1])
        return
    client = Client(ip)
    HUB.join(client)

    async def sender():
        while True:
            await websocket.send_text(await client.queue.get())

    send_task = asyncio.create_task(sender())
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
                reply = HUB.subscribe(client, msg.get("op"), msg.get("ch") or [])
            except (ValueError, AttributeError, TypeError):
                reply = {"error": "bad_json"}
            await websocket.send_json(reply)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        send_task.cancel()
        HUB.leave(client)
