"""Sendler Dash — holders of launch tokens (/v2/dash/tokens/{id}/holders).

The indexer's DashWorker keeps `dash_holders` (balance per token × account from the token's NEP-141 events, `bal`
Decimal128 raw units) and `dash_holder_stats` (holder count, hourly series, `complete` / `seeded`) —
indexer/services/dashboard/holders.py, contract indexer/docs/DASH_DATA.md. Only launch tokens (Nearly / JustHoot / Intear) are
tracked. A token whose history is neither complete (mint seen live) nor reconciled with a FastNear snapshot
(`seeded`) is not shown: its balances are partial.

Shares are of the circulating supply = the sum of positive balances (burnt tokens are gone). Pools, launchpads,
the token contract itself and other contracts are tagged and left out of «top 10» — what a trader means by
concentration is wallets.
"""
from __future__ import annotations

from config import dash as cfg
from services import dash_service as svc

# kind of dash_labels / label_of → tag of a holder row
POOL_KINDS = {"dex"}
CONTRACT_KINDS = {"aggregator", "bridge", "launchpad", "token", "contract"}
CUSTODY_KINDS = {"exchange", "hub"}


def raw(v) -> int:
    """bal from Mongo (Decimal128, int or str) → int."""
    if v is None:
        return 0
    if hasattr(v, "to_decimal"):
        return int(v.to_decimal())
    return int(v)


def tag_of(account: str, token: str, label: dict | None) -> str | None:
    if account == token:
        return "contract"  # Nearly's tax accumulates on the token contract itself
    kind = (label or {}).get("kind")
    if kind in POOL_KINDS:
        return "pool"
    if kind in CONTRACT_KINDS:
        return "contract"
    if kind in CUSTODY_KINDS:
        return kind
    return None


async def linked_to(creator: str | None) -> set[str]:
    """Wallets linked to the creator in the sniper's graph (sniper_dev_links): those whose link root is the creator
    and the roots the creator himself is linked to."""
    if not creator:
        return set()
    db = svc._db()
    out = {d["_id"] async for d in db.sniper_dev_links.find({"links.root": creator}, {"_id": 1}).limit(500)}
    own = await db.sniper_dev_links.find_one({"_id": creator}, {"links": 1}) or {}
    out |= {x.get("root") for x in own.get("links") or [] if x.get("root")}
    out.discard(creator)
    return out


async def holders(tid: str, limit: int) -> dict:
    db = svc._db()
    stats = await db.dash_holder_stats.find_one({"_id": tid}) or {}
    if not stats:
        launch = await db.dash_launches.find_one({"_id": tid}, {"_id": 1})
        return {"token": tid, "available": False, "reason": "pending" if launch else "not_tracked"}
    if not (stats.get("complete") or stats.get("seeded")):
        return {"token": tid, "available": False, "reason": "pending"}

    supply = 0
    async for s in db.dash_holders.aggregate([{"$match": {"token": tid, "bal": {"$gt": 0}}},
                                              {"$group": {"_id": None, "s": {"$sum": "$bal"}}}]):
        supply = raw(s["s"])
    docs = [d async for d in db.dash_holders.find({"token": tid, "bal": {"$gt": 0}}, {"account": 1, "bal": 1})
            .sort("bal", -1).limit(max(limit, cfg.HOLDERS_MAX))]
    launch = await db.dash_launches.find_one({"_id": tid}, {"creator": 1}) or {}
    creator = launch.get("creator")
    linked = await linked_to(creator)
    smart = set(await svc.smart_ids())
    labels = await svc.label_map(d["account"] for d in docs)
    dec = (await svc.meta([tid]))[tid].get("decimals")

    # the dev and linked wallets - by their own records, not by the top: a dev may have sold and dropped out of the first hundred
    dev, linked_sum = 0, 0
    ids = [f"{tid}:{a}" for a in ([creator] if creator else []) + sorted(linked)]
    if ids:
        async for d in db.dash_holders.find({"_id": {"$in": ids}, "bal": {"$gt": 0}}, {"account": 1, "bal": 1}):
            if d["account"] == creator:
                dev = raw(d["bal"])
            else:
                linked_sum += raw(d["bal"])
    rows, wallets_top, contracts = [], [], 0
    for d in docs:
        acc, bal = d["account"], raw(d["bal"])
        label = svc.label_of(acc, labels)
        kind = tag_of(acc, tid, label)
        tags = [kind] if kind else []
        if acc == creator:
            tags.append("creator")
        elif acc in linked:
            tags.append("linked")
        if acc in smart:
            tags.append("smart")
        if kind in ("pool", "contract"):
            contracts += bal
        elif len(wallets_top) < 10:
            wallets_top.append(bal)
        rows.append({"account": acc, "bal": str(bal),
                     "amount": bal / 10 ** dec if dec is not None else None,
                     "share": bal / supply if supply else None, "label": label, "tags": tags})

    def share(x: int) -> float | None:
        return x / supply if supply else None

    return {"token": tid, "available": True, "complete": bool(stats.get("complete")),
            "top_only": bool(stats.get("top_only")),  # seeded from a 100-row snapshot: count is a lower bound
            "holders": stats.get("holders"), "updated_at": stats.get("updated_at"),
            "supply": str(supply), "decimals": dec, "creator": creator,
            "top10_share": share(sum(wallets_top)), "dev_share": share(dev), "linked_share": share(linked_sum),
            "contracts_share": share(contracts),  # pools, launchpads, the token contract (only among the top rows)
            "series": [{"t": p.get("t"), "n": p.get("n")} for p in stats.get("series") or []],
            "rows": rows[:limit]}
