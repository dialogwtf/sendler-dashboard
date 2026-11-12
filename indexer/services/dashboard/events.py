"""Event feed for the WebSocket (dash_events) and account labels (dash_labels) - Sendler Dash.

dash_events is a capped collection: the worker inserts events in one batch per flush, the API holds a tailable cursor
and fans them out to channel subscribers. Neither Redis nor a replica set is needed; old events are evicted by
themselves (SIZE / MAX_DOCS). Channels: launch, trade, move, new, network.

dash_labels: known contracts - on worker start (source manual), new contracts - as they appear
(source auto, kind contract), hubs - kind hub (sync_hub_labels). Exchanges and teams are added by hand
(source manual, scripts/dash_labels.py) and are never touched automatically.
"""
from __future__ import annotations

import datetime as dt
import logging

from pymongo import UpdateMany, UpdateOne

logger = logging.getLogger("dashboard")

EVENTS = "dash_events"
SIZE = 64 * 1024 * 1024
MAX_DOCS = 200_000

# label - a proper name only (no language) or None; what the account is, says kind, and the site translates it
# itself: dex, aggregator, bridge, launchpad, token, contract, hub, exchange, team, wallet.
SEED_LABELS = {
    "dclv2.ref-labs.near": ("Rhea DCL", "dex"),
    "v2.ref-finance.near": ("Rhea", "dex"),
    "dex.intear.near": ("Intear DEX", "dex"),
    "aggregatedex.near": (None, "aggregator"),
    "wrap.near": ("wNEAR", "contract"),
    "intents.near": ("NEAR Intents", "bridge"),
    "factory.bridge.near": ("Rainbow Bridge", "bridge"),
    "omft.near": ("Omni Bridge", "bridge"),
    "nearlytrade.near": ("Nearly", "launchpad"),
    "launchpad.justhoot.near": ("JustHoot", "launchpad"),
    "v2-launchpad.justhoot.near": ("JustHoot v2", "launchpad"),
    "v2-pool-creator.justhoot.near": ("JustHoot", "launchpad"),
    "launch-v2.intear.near": ("Intear Launch", "launchpad"),
}


async def ensure_events(db) -> None:
    """Create the capped events collection if it does not exist."""
    try:
        if EVENTS not in await db.list_collection_names(filter={"name": EVENTS}):
            await db.create_collection(EVENTS, capped=True, size=SIZE, max=MAX_DOCS)
    except Exception as e:  # noqa: BLE001 - a race of two processes or no rights: live mode without the feed
        logger.warning("Sendler Dash: %s not created: %s", EVENTS, e)


LEGACY_TEXT_LABELS = ("Крупный узел", "Агрегатор DEX")  # Russian descriptions stored before 2026-11-09 - now it is kind


async def seed_labels(db) -> None:
    """Seed of known accounts. Our own records (source manual from this list) are brought to the current shape; old
    Russian descriptions in label are removed - kind replaces them."""
    now = dt.datetime.now(dt.timezone.utc)
    ops = [UpdateOne({"_id": acc}, {"$set": {"label": label, "kind": kind, "source": "manual", "updated_at": now}},
                     upsert=True) for acc, (label, kind) in SEED_LABELS.items()]
    ops.append(UpdateMany({"label": {"$in": list(LEGACY_TEXT_LABELS)}}, {"$set": {"label": None, "updated_at": now}}))
    try:
        await db.dash_labels.bulk_write(ops, ordered=False)
    except Exception as e:  # noqa: BLE001
        logger.warning("Sendler Dash: labels not written: %s", e)


HUB_LABEL = None  # a hub has no name: whose exchange it is, is unknown; the site says "large hub" by kind


async def sync_hub_labels(db) -> int:
    """Hubs (sniper_dev_links `hub:<account>`, written by the Sendler trade bot - more than 50 NEAR counterparties a day:
    exchanges, bridges, services) -> label kind hub, source auto. Whose exchange it is cannot be told from the chain -
    names are set by hand (scripts/dash_labels.py), $setOnInsert leaves a manual label alone. Returns hubs seen."""
    try:
        now = dt.datetime.now(dt.timezone.utc)
        ops = [UpdateOne({"_id": d["_id"][4:]}, {"$setOnInsert": {"label": HUB_LABEL, "kind": "hub", "source": "auto",
                                                                  "updated_at": now}}, upsert=True)
               async for d in db.sniper_dev_links.find({"_id": {"$regex": "^hub:"}}, {"_id": 1})]
        if ops:
            await db.dash_labels.bulk_write(ops, ordered=False)
        return len(ops)
    except Exception as e:  # noqa: BLE001 - labels are not critical
        logger.warning("Sendler Dash: hub labels not written: %s", e)
        return 0


def event(ch: str, data: dict, at: dt.datetime | None = None) -> dict:
    return {"ch": ch, "ts": at or dt.datetime.now(dt.timezone.utc), "d": data}
