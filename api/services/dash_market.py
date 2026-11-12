"""NFT market data behind the dashboard's NFT widget (services/dash_nft.py): the collections overview and the trades
feed of sendler-market. Collections `collection_stats`, `collection_stats_daily`, `market_trades` and
`allowed_nft_contracts` are written by the Sendler NFT indexer; without them the widget is simply empty.
"""
from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from core.database import Database

_META_TTL_S = 300.0
_meta: Tuple[float, List[dict]] = (0.0, [])


def _map_id(docs: List[dict]) -> List[dict]:
    for doc in docs:
        if "_id" in doc and doc["_id"] is not None:
            doc["_id"] = str(doc["_id"])
    return docs


def _utc_day(offset_days: int = 0) -> str:
    """UTC day key YYYY-MM-DD, offset_days back from today."""
    return (datetime.now(timezone.utc) - timedelta(days=offset_days)).strftime("%Y-%m-%d")


def _floor_change_pct(floor_now: Optional[float], floor_prev: Optional[float]) -> Optional[float]:
    """Change vs yesterday's daily snapshot, %. None when either side is missing/zero."""
    if floor_now is None or not floor_prev:
        return None
    return round((floor_now - floor_prev) / floor_prev * 100, 2)


def _utc_ms(dt: datetime) -> int:
    """Epoch ms. Motor returns naive UTC datetimes — treat naive as UTC, not local."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _encode_trades_cursor(boundary_ts: datetime, boundary_ids: List[str]) -> str:
    """Opaque page cursor: boundary block_timestamp + _ids already returned at it.

    BSON Dates have ms precision, so epoch ms round-trips exactly. Keeping the
    boundary-timestamp _ids in the cursor lets the next page use `$lte + $nin`
    instead of a (block_timestamp, _id) tie-break sort the indexes can't serve —
    same-block trades share block_timestamp, so a plain `$lt` would drop them.
    """
    raw = json.dumps({"ts": _utc_ms(boundary_ts), "ids": boundary_ids})
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_trades_cursor(cursor: str) -> Tuple[datetime, List[str]]:
    """Inverse of _encode_trades_cursor. Raises ValueError on any malformed input."""
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        ts = datetime.fromtimestamp(int(data["ts"]) / 1000, tz=timezone.utc)
        ids = data.get("ids") or []
        if not isinstance(ids, list):
            raise TypeError("ids must be a list")
        return ts, [str(i) for i in ids]
    except Exception:
        raise ValueError("Invalid cursor")


async def _collections_meta() -> List[dict]:
    """Curated collections: name, image, description, verified. Cached for 5 minutes."""
    global _meta
    if _meta[0] > time.monotonic():
        return _meta[1]
    db = Database.get_db("near_indexer")
    out = []
    async for doc in db["allowed_nft_contracts"].find(
            {}, {"_id": 1, "nft_contract_id": 1, "collection_name": 1, "collection_image": 1,
                 "collection_description": 1, "verified": 1}):
        cid = doc.get("nft_contract_id") or doc.get("_id")
        out.append({"contract_id": str(cid), "media": doc.get("collection_image"),
                    "collection_name": doc.get("collection_name"),
                    "collection_description": doc.get("collection_description"),
                    "verified": bool(doc.get("verified", False))})
    _meta = (time.monotonic() + _META_TTL_S, out)
    return out


async def get_market_collections(
    sort_by: str = "volume_24h_usd",
    order: int = -1,
    skip: int = 0,
    limit: int = 50,
) -> Tuple[List[dict], int]:
    """Collections overview from collection_stats (one find, no on-the-fly aggregation).

    Enriched with collection_name/collection_image/verified from allowed_nft_contracts
    (cached) and prev_floor_usd/floor_change_24h_pct from yesterday's daily snapshots
    (one $in query per page).
    """
    db = Database.get_db("near_indexer")
    collection = db["collection_stats"]
    total = await collection.count_documents({})
    items = (
        await collection.find({})
        .sort([(sort_by, order), ("_id", 1)])
        .skip(skip)
        .limit(limit)
        .to_list(limit)
    )

    yesterday = _utc_day(1)
    daily_ids = [f"{it.get('nft_contract_id') or it.get('_id')}::{yesterday}" for it in items]
    prev_floors: Dict[str, Optional[float]] = {}
    if daily_ids:
        prev_docs = await db["collection_stats_daily"].find(
            {"_id": {"$in": daily_ids}}, {"nft_contract_id": 1, "floor_usd": 1}
        ).to_list(None)
        prev_floors = {d["nft_contract_id"]: d.get("floor_usd") for d in prev_docs}

    meta_by_contract = {m["contract_id"]: m for m in await _collections_meta()}
    for item in items:
        contract_id = item.get("nft_contract_id") or item.get("_id")
        meta = meta_by_contract.get(contract_id)
        if meta:
            item["collection_name"] = meta.get("collection_name")
            item["collection_image"] = meta.get("media")
            item["collection_description"] = meta.get("collection_description")
        item["verified"] = bool(meta.get("verified")) if meta else False
        item["prev_floor_usd"] = prev_floors.get(contract_id)
        item["floor_change_24h_pct"] = _floor_change_pct(
            item.get("floor_usd"), item["prev_floor_usd"]
        )
    return _map_id(items), total


async def get_trades(limit: int = 50, cursor: Optional[str] = None) -> Tuple[List[dict], bool, Optional[str]]:
    """Trades feed from market_trades (permanent history), newest first.

    Pagination: `cursor` — the next_cursor of the previous page. The cursor survives new trades
    being indexed: pages are pinned below its block_timestamp.
    Returns (items, has_more, next_cursor); next_cursor is None on the last page.
    """
    boundary_ts, seen_ids = None, []
    if cursor:
        boundary_ts, seen_ids = _decode_trades_cursor(cursor)
    db = Database.get_db("near_indexer")
    query: dict = {}
    if boundary_ts is not None:
        query["block_timestamp"] = {"$lte": boundary_ts}
        if seen_ids:
            query["_id"] = {"$nin": seen_ids}
    items = (
        await db["market_trades"]
        .find(query)
        .sort("block_timestamp", -1)
        .limit(limit + 1)
        .to_list(limit + 1)
    )
    has_more = len(items) > limit
    items = items[:limit]

    next_cursor = None
    if has_more and items and items[-1].get("block_timestamp") is not None:
        last_ts = items[-1]["block_timestamp"]
        boundary_ids = [
            str(d["_id"]) for d in items if d.get("block_timestamp") == last_ts
        ]
        if boundary_ts is not None and _utc_ms(last_ts) == _utc_ms(boundary_ts):
            # page ended on the same timestamp the cursor started at — keep
            # carrying the earlier ids or the next page would re-return them
            boundary_ids = seen_ids + boundary_ids
        next_cursor = _encode_trades_cursor(last_ts, boundary_ids)
    return _map_id(items), has_more, next_cursor
