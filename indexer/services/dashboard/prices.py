"""Rates for valuing amounts in $ (Sendler Dash): $ per raw unit of a token.

- ft_prices (REF API, services/dashboard/reference.py): price_usd and decimals - one query every REFRESH_S;
- for tokens without a rate in ft_prices (launch memes) - the last trade price in NEAR from the stream × the NEAR rate.
REF is wrong for some tokens (PRL +68%, aiNEAR) - for them the stream's trade price is more accurate, so it wins
when fresh (FRESH_S). No RPC.

`near_per_raw` - the pair asset's price in NEAR for trades in pairs not against NEAR (trades.trade_of `via`): a
stable - through the NEAR rate in $ (a stable without a rate - $1), the rest - only a stream price not older than
QUOTE_FRESH_S: REF is wrong on PRL and aiNEAR, better to skip a trade than to record it at a foreign price.
"""
from __future__ import annotations

import logging
import time

from services.chain.swaps import KNOWN

logger = logging.getLogger("dashboard")

NEAR = "near"
WNEAR = "wrap.near"
REFRESH_S = 300.0
FRESH_S = 3600.0
QUOTE_FRESH_S = 6 * 3600.0  # a pair asset (PRL, QTC…) trades against NEAR less often than memes
STABLE_DECIMALS = {a: dec for a, (_, dec) in KNOWN.items() if a != NEAR}


class UsdBook:
    def __init__(self, db) -> None:
        self.db = db
        self._ref: dict[str, float] = {}  # token -> $ per raw unit
        self._near_usd: float | None = None
        self._trade: dict[str, tuple[float, float]] = {}  # token -> (NEAR per raw unit, when)
        self._loaded = 0.0

    @property
    def near_usd(self) -> float | None:
        return self._near_usd

    async def refresh(self, force: bool = False) -> None:
        if not force and time.monotonic() - self._loaded < REFRESH_S:
            return
        self._loaded = time.monotonic()
        ref: dict[str, float] = {}
        try:
            async for d in self.db.ft_prices.find({"price_usd": {"$gt": 0}}, {"price_usd": 1, "decimals": 1}):
                dec = d.get("decimals")
                if isinstance(dec, int) and 0 <= dec <= 36:
                    ref[d["_id"]] = float(d["price_usd"]) / 10 ** dec
        except Exception as e:  # noqa: BLE001 - without rates we value what we can
            logger.warning("Sendler Dash: ft_prices not read: %s", e)
            return
        self._ref = ref
        if WNEAR in ref:
            self._near_usd = ref[WNEAR] * 1e24

    def note_trade(self, token: str, px_near: float, ts: float) -> None:
        self._trade[token] = (px_near, ts)
        if len(self._trade) > 50_000:
            cut = time.time() - FRESH_S
            self._trade = {t: v for t, v in self._trade.items() if v[1] >= cut}

    def usd_per_raw(self, token: str, now: float | None = None) -> float | None:
        if token in (NEAR, WNEAR):
            return self._near_usd / 1e24 if self._near_usd else None
        hit = self._trade.get(token)
        if hit and self._near_usd and (now or time.time()) - hit[1] < FRESH_S:
            return hit[0] * self._near_usd
        return self._ref.get(token)

    def near_per_raw(self, token: str, now: float | None = None) -> float | None:
        """NEAR per raw unit of the pair asset; None - no price (the trade is not counted)."""
        if token in STABLE_DECIMALS:
            if not self._near_usd:
                return None
            usd = self._ref.get(token) or 1.0 / 10 ** STABLE_DECIMALS[token]
            return usd / self._near_usd
        hit = self._trade.get(token)
        if hit and (now or time.time()) - hit[1] < QUOTE_FRESH_S:
            return hit[0]
        return None

    def usd(self, token: str, raw: int, now: float | None = None) -> float | None:
        per = self.usd_per_raw(token, now)
        return raw * per if per is not None else None
