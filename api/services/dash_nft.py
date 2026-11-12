"""Sendler Dash — NFT market widget (/v2/dash/nft/*): sales, volume, top collections of sendler-market.

A thin public layer over the market's own data (services/dash_market.py: collection_stats,
collection_stats_daily, market_trades — written by the indexer): /v2/market sits behind X-API-Key for the market
site, the dashboard calls from the browser without a key. Read-only, cached by the dash route.
"""
from __future__ import annotations

import datetime as dt

from services import dash_market as market
from services import dash_service as svc

SORTS = {"volume_24h": "volume_24h_usd", "volume_7d": "volume_7d_usd", "volume_30d": "volume_30d_usd",
         "sales_24h": "sales_24h", "floor": "floor_usd", "last_trade": "last_trade_at"}


async def collections(sort: str, limit: int) -> list[dict]:
    if sort not in SORTS:
        raise svc.DashError("bad_sort")
    items, _ = await market.get_market_collections(SORTS[sort], -1, 0, limit)
    return items


async def sales(limit: int, cursor: str | None) -> dict:
    try:
        items, has_more, next_cursor = await market.get_trades(limit=limit, cursor=cursor)
    except ValueError:
        raise svc.DashError("bad_cursor")
    return {"items": items, "next_cursor": next_cursor if has_more else None}


async def stats(days: int) -> dict:
    """Market totals now (sum of collection_stats) and per UTC day for `days` (sum of collection_stats_daily)."""
    db = svc._db()
    totals = {}
    async for t in db.collection_stats.aggregate([{"$group": {
            "_id": None, "volume_24h_usd": {"$sum": "$volume_24h_usd"}, "volume_7d_usd": {"$sum": "$volume_7d_usd"},
            "sales_24h": {"$sum": "$sales_24h"}, "listings": {"$sum": "$listings_count"},
            "active_24h": {"$sum": {"$cond": [{"$gt": ["$sales_24h", 0]}, 1, 0]}}, "collections": {"$sum": 1}}}]):
        totals = {k: v for k, v in t.items() if k != "_id"}
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days - 1)).strftime("%Y-%m-%d")
    daily = [{"date": d["_id"], "volume_usd": d["v"], "sales": d["n"]} async for d in db.collection_stats_daily.aggregate([
        {"$match": {"date": {"$gte": since}}},
        {"$group": {"_id": "$date", "v": {"$sum": "$volume_usd"}, "n": {"$sum": "$sales_count"}}},
        {"$sort": {"_id": 1}}])]
    return {**totals, "daily": daily}
