"""Reference data the dashboard needs besides the block stream: USD rates and token metadata.

- FtPrices: every FT_PRICES_S the REF Finance price list (one HTTP request) -> `ft_prices` {_id: token, price_usd,
  symbol, decimals}. The worker turns NEAR amounts into USD with the wrap.near rate and prices launch tokens through
  their last trade (services/dashboard/prices.py);
- TokenMeta: tokens that were traded or launched recently and have no `ft_metadata` document yet get one from the
  contract's `ft_metadata` view (NEP-148): symbol, name, decimals, icon. One RPC call per token, ever - these are the
  only RPC calls of the indexer, and none of them is on the block path.

In production both collections are filled by the Sendler marketplace indexer; here they are part of the dashboard so
it runs on its own.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time

import aiohttp
from pymongo import UpdateOne

logger = logging.getLogger("dashboard")

FT_PRICES_S = 60.0
META_EVERY_S = 10.0
META_BATCH = 25  # RPC calls per cycle
META_RETRY_S = 600.0  # a token whose metadata could not be read is asked again after this
META_RECENT_S = 900.0  # tokens traded within this window
ICON_MAX = 64 * 1024  # bigger data: URIs are dropped - the icon is optional, the document must stay small
TEXT_MAX = 64
HARD_CODED = {"near": {"symbol": "NEAR", "name": "NEAR", "decimals": 24, "icon": None}}


class FtPrices:
    def __init__(self, db, session: aiohttp.ClientSession, url: str) -> None:
        self.col = db["ft_prices"]
        self.session = session
        self.url = url

    async def poll_once(self) -> int:
        async with self.session.get(self.url, timeout=aiohttp.ClientTimeout(total=15)) as r:
            r.raise_for_status()
            data = await r.json(content_type=None)
        now = dt.datetime.now(dt.timezone.utc)
        ops = []
        for token, entry in (data if isinstance(data, dict) else {}).items():
            try:
                price = float(entry.get("price"))
            except (AttributeError, TypeError, ValueError):
                continue
            if not price > 0:
                continue
            ops.append(UpdateOne({"_id": token}, {"$set": {"price_usd": price, "symbol": entry.get("symbol"),
                                                           "decimals": entry.get("decimal"), "updated_at": now}},
                                 upsert=True))
        if ops:
            await self.col.bulk_write(ops, ordered=False)
        return len(ops)

    async def run(self) -> None:
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - the next cycle retries
                logger.warning("ft prices: %s", str(e)[:200])
            await asyncio.sleep(FT_PRICES_S)


def clean_meta(raw: dict) -> dict | None:
    """Contract's ft_metadata -> our document, or None when it is not a token's metadata. Everything in it is written
    by the token's creator: sizes are capped, types are checked."""
    try:
        decimals = int(raw.get("decimals"))
    except (TypeError, ValueError):
        return None
    if not 0 <= decimals <= 36:
        return None
    symbol, name, icon = raw.get("symbol"), raw.get("name"), raw.get("icon")
    if not isinstance(symbol, str) or not symbol.strip():
        return None
    return {"symbol": symbol.strip()[:TEXT_MAX],
            "name": name.strip()[:TEXT_MAX] if isinstance(name, str) else None,
            "decimals": decimals,
            "icon": icon if isinstance(icon, str) and 0 < len(icon) <= ICON_MAX else None}


class TokenMeta:
    def __init__(self, db, session: aiohttp.ClientSession, rpc_url: str) -> None:
        self.db = db
        self.col = db["ft_metadata"]
        self.session = session
        self.rpc_url = rpc_url
        self._known: set[str] = set()  # tokens that have a document
        self._retry: dict[str, float] = {}  # token -> not before (monotonic)

    async def fetch(self, token: str) -> dict | None:
        """Returns the cleaned metadata; {} - the contract answered with something else (not a token); None - no answer."""
        payload = {"jsonrpc": "2.0", "id": "dash", "method": "query",
                   "params": {"request_type": "call_function", "finality": "final", "account_id": token,
                              "method_name": "ft_metadata", "args_base64": "e30="}}
        async with self.session.post(self.rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status != 200:
                return None
            body = await r.json(content_type=None)
        result = (body or {}).get("result") or {}
        if "result" not in result:
            return None  # no such account or method yet: ask later
        try:
            raw = json.loads(bytes(result["result"]).decode())
        except (ValueError, TypeError):
            return {}
        return (clean_meta(raw) or {}) if isinstance(raw, dict) else {}

    async def candidates(self) -> list[str]:
        since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=META_RECENT_S)
        tokens = set(await self.db.dash_trades.distinct("token", {"ts": {"$gte": since}}))
        tokens |= {d["_id"] async for d in self.db.dash_launches.find({"updated_at": {"$gte": since}}, {"_id": 1})}
        now = time.monotonic()
        todo = [t for t in tokens if isinstance(t, str) and t not in self._known and self._retry.get(t, 0) <= now]
        if not todo:
            return []
        have = {d["_id"] async for d in self.col.find({"_id": {"$in": todo}}, {"_id": 1})}
        self._known |= have
        return [t for t in todo if t not in have][:META_BATCH]

    async def poll_once(self) -> int:
        written = 0
        for token in await self.candidates():
            meta = HARD_CODED.get(token)
            if meta is None:
                try:
                    meta = await self.fetch(token)
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                    meta = None
            if not meta:
                # not a token ({}) - a day; no answer (None) - a few minutes
                self._retry[token] = time.monotonic() + (86400.0 if meta == {} else META_RETRY_S)
                continue
            await self.col.update_one({"_id": token}, {"$set": meta}, upsert=True)
            self._known.add(token)
            written += 1
        if len(self._retry) > 50_000:
            self._retry.clear()
        return written

    async def run(self) -> None:
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning("token metadata: %s", str(e)[:200])
            await asyncio.sleep(META_EVERY_S)

