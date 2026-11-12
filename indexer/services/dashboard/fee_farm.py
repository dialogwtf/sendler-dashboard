""""Fee farm" launches: the creator risks nothing and earns the token's transfer tax from every bot that buys and sells.

Two shapes, measured on mainnet (Nearly: sageai.near, 26-27 Oct 2026 - 6 launches bought by bots only; JustHoot over
14 days to 2 Nov 2026 - 121 positions, -7% on average, not a single x2):

- Nearly: no dev buy, and the whole tax (minus the platform's cut) goes to the creator;
- JustHoot: no dev buy, the token has a transfer tax and the creator gets a share of it.

Returns the reason as text (stored in `dash_launches.fee_farm`; the API exposes a flag), or None - also when the
launch arguments are unknown (fallback path).
"""
from __future__ import annotations

from .feed import LaunchEvent


def fee_farm(ev: LaunchEvent) -> str | None:
    x = ev.extra or {}
    if ev.launchpad == "justhoot":
        return _hoot_fee_farm(x)
    tax = x.get("tax")
    if ev.launchpad != "nearly" or not isinstance(tax, dict) or x.get("dev_buy") is None:
        return None
    try:
        dev, buy, sell = int(x["dev_buy"]), int(tax.get("buy_bps") or 0), int(tax.get("sell_bps") or 0)
        creator = int(tax.get("creator_bps") or 0)
        others = int(tax.get("holders_bps") or 0) + int(tax.get("burn_bps") or 0)
    except (TypeError, ValueError):
        return None
    if dev or not (buy or sell) or creator < 10000 or others:
        return None
    return f"no dev buy, the whole tax ({max(buy, sell) / 100:g}%) goes to the creator"


def _hoot_fee_farm(x: dict) -> str | None:
    shares = x.get("shares")
    if not isinstance(shares, dict):  # no launch arguments (fallback path, old launcher)
        return None
    try:
        dev, fee = int(x.get("dev_buy") or 0), int(x.get("fee_pips") or 0)
        owner = int(shares.get("token_owner_bps") or 0)
    except (TypeError, ValueError):
        return None
    if dev or not fee or not owner:
        return None
    return f"no dev buy, tax {fee / 10000:g}%, creator's share of it {owner / 100:g}%"
