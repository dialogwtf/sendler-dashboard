"""
Holders of Sendler Dash launches from before their collection started (services/dashboard/holders.py): reconciles
dash_holders with the FastNear snapshot `/v1/ft/<token>/top` (up to 100 holders, no key).

The live stream keeps balances from the mint, and a token whose mint it saw is complete (`dash_holder_stats.complete`).
Launches started before the collection was deployed have only the increments after it - the script brings these
tokens to the snapshot: correction = snapshot - recorded (`$inc`), so a re-run is safe and the worker may write in
parallel (the only discrepancy is transfers between reading the base and the snapshot; a re-run removes them). A
snapshot shorter than 100 is all the holders, the rest are zeroed; exactly 100 - the top is known (`top_only: true`),
the holder count is a lower bound. Until a token is reconciled (`seeded`) the API does not show its holders.

    python scripts/backfill_dash_holders.py --dry-run     # count the corrections
    python scripts/backfill_dash_holders.py               # launches not reconciled yet
    python scripts/backfill_dash_holders.py --token x.nearlytrade.near --force

One FastNear request per token, at most RATE per second; no RPC.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiohttp
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne

from config import Settings
from services.dashboard.holders import SNAPSHOT_LIMIT, corrections, dec, holder_id, stats_op

API = "https://api.fastnear.com/v1/ft/{}/top"
RATE = 4.0  # requests per second


async def snapshot(http: aiohttp.ClientSession, token: str) -> tuple[dict[str, int], int] | None:
    """(non-zero balances, number of entries in the answer): FastNear also returns those who sold everything (balance 0),
    so whether the list is cut off is seen by the number of entries, not of non-zero ones."""
    for attempt in range(3):
        try:
            async with http.get(API.format(token), timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status == 429:
                    await asyncio.sleep(5 * (attempt + 1))
                    continue
                if r.status != 200:
                    return None
                data = await r.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            await asyncio.sleep(2)
            continue
        out = {}
        accounts = data.get("accounts") or []
        for a in accounts:
            try:
                bal = int(a.get("balance") or 0)
            except (TypeError, ValueError):
                continue
            if isinstance(a.get("account_id"), str) and bal > 0:
                out[a["account_id"]] = bal
        return out, len(accounts)
    return None


async def stored(db, token: str) -> dict[str, int]:
    out = {}
    async for d in db.dash_holders.find({"token": token}, {"account": 1, "bal": 1}):
        out[d["account"]] = int(d["bal"].to_decimal()) if hasattr(d.get("bal"), "to_decimal") else int(d.get("bal") or 0)
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", action="append", help="only these tokens (may be repeated)")
    ap.add_argument("--force", action="store_true", help="reconcile complete / already reconciled tokens too")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    db = AsyncIOMotorClient(Settings.MONGO_URI, tz_aware=True)[Settings.MONGO_DB_NAME]

    stats = {d["_id"]: d async for d in db.dash_holder_stats.find({}, {"complete": 1, "seeded": 1})}
    if args.token:
        tokens = args.token
    else:
        tokens = [d["_id"] async for d in db.dash_launches.find({}, {"_id": 1}).sort("t_first", -1)]
    todo = [t for t in tokens if args.force or not (stats.get(t, {}).get("complete") or stats.get(t, {}).get("seeded"))]
    print(f"tokens: {len(tokens)}, to reconcile: {len(todo)}")

    done = changed = failed = 0
    async with aiohttp.ClientSession(headers={"User-Agent": "sendler-dash-backfill"}) as http:
        for i, token in enumerate(todo, 1):
            await asyncio.sleep(1 / RATE)
            got = await snapshot(http, token)
            if got is None:
                failed += 1
                print(f"  {token}: no FastNear snapshot")
                continue
            snap, listed = got
            top_only = listed >= SNAPSHOT_LIMIT
            fix = corrections(await stored(db, token), snap, full=not top_only)
            now = dt.datetime.now(dt.timezone.utc)
            if fix:
                changed += 1
            if not args.dry_run:
                if fix:
                    await db.dash_holders.bulk_write([UpdateOne(
                        {"_id": holder_id(token, a)},
                        {"$inc": {"bal": dec(v)}, "$set": {"updated_at": now},
                         "$setOnInsert": {"token": token, "account": a, "first_at": now}}, upsert=True)
                        for a, v in fix.items()], ordered=False)
                n = await db.dash_holders.count_documents({"token": token, "bal": {"$gt": 0}})
                await db.dash_holder_stats.bulk_write([stats_op(token, n, now, None), UpdateOne(
                    {"_id": token}, {"$set": {"seeded": now, "src": "fastnear", "top_only": top_only}})])
            done += 1
            if i % 50 == 0 or args.token:
                print(f"  {i}/{len(todo)} {token}: holders in the snapshot {len(snap)}{' (top)' if top_only else ''}, corrections {len(fix)}")
    print(f"reconciled {done}, with corrections {changed}, no snapshot {failed}" + (" (dry-run)" if args.dry_run else ""))


if __name__ == "__main__":
    asyncio.run(main())
