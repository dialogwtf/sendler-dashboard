"""
Sendler Dash account labels (dash_labels) by hand: exchanges, teams, known wallets. A manual label (source manual)
outranks automatic ones - the worker does not overwrite it (hubs and new contracts are written only if there is none).

    python scripts/dash_labels.py list [kind]               # all, or one kind
    python scripts/dash_labels.py set <account> "<name>" exchange
    python scripts/dash_labels.py del <account>

Kinds: exchange, dex, aggregator, bridge, launchpad, token, contract, hub, team, wallet. The name is a proper name
only (Binance, Rhea DCL), no language: the site translates the kind itself.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from motor.motor_asyncio import AsyncIOMotorClient

from config import Settings

KINDS = ("exchange", "dex", "aggregator", "bridge", "launchpad", "token", "contract", "hub", "team", "wallet")


async def main(argv: list[str]) -> int:
    db = AsyncIOMotorClient(Settings.MONGO_URI)[Settings.MONGO_DB_NAME]
    cmd = argv[0] if argv else "list"
    if cmd == "list":
        flt = {"kind": argv[1]} if len(argv) > 1 else {}
        async for d in db.dash_labels.find(flt).sort([("kind", 1), ("_id", 1)]):
            print(f"{d.get('kind', ''):10} {d.get('source', ''):6} {d['_id']}  {d.get('label') or ''}")
        return 0
    if cmd == "set" and len(argv) == 4:
        account, label, kind = argv[1].strip().lower(), argv[2].strip(), argv[3].strip()
        if kind not in KINDS:
            print(f"kind - one of: {', '.join(KINDS)}")
            return 2
        await db.dash_labels.update_one({"_id": account}, {"$set": {
            "label": label, "kind": kind, "source": "manual", "updated_at": dt.datetime.now(dt.timezone.utc)}},
            upsert=True)
        print(f"{account}: {label} ({kind})")
        return 0
    if cmd == "del" and len(argv) == 2:
        r = await db.dash_labels.delete_one({"_id": argv[1].strip().lower()})
        print("deleted" if r.deleted_count else "no such label")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
