"""Smart money: wallets that keep making money on NEAR tokens - found from the block stream alone, no RPC.

- every swap of the stream (Rhea DCL, Intear, Rhea v1 - services.chain.swaps.swap_legs) is netted per transaction;
  a NEAR <-> token pair is a buy or a sell for that much NEAR. The wallet is the swap's actor, and for an aggregator
  contract (>= 3 different signers) - the signer. Venue and launchpad contracts and a creator in his own token are
  not counted;
- sniper_smart_flows {_id: wallet:token, near_in, near_out (NEAR), tok_in, tok_out (raw, float), buys, sells,
  first_at, last_at, expires_at} - one $inc batch per block; sniper_smart_prices - the token's last price
  (the collection names are historical: the same data feeds the Sendler trade bot);
- hourly - a 30-day score: profit of the matched part + the rest at the last price; tokens sold beyond what was
  bought (dev allocation, airdrop) have no cost basis and are ignored. "Smart" - the first TOP by profit among the
  wallets that pass the thresholds (sniper_smart_wallets).

The API serves the ranking (/smart/top), the wallets' trades (/smart/feed) and their position in a token.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from dataclasses import dataclass, field

from pymongo import ASCENDING, DESCENDING, UpdateOne

from services.chain.block import DCL, INTEAR_ENGINE, NEARLY, REF, WNEAR, header, outcomes, signer
from services.chain.swaps import NEAR as NEAR_ASSET
from services.chain.swaps import QUOTES, VENUES, Leg, net_flow, swap_legs

logger = logging.getLogger("dashboard")

NEAR = 10 ** 24  # yocto per NEAR
SETTLE_BLOCKS = 3  # an aggregator route: its legs arrive 1-2 blocks apart

FLOWS_TTL_S = 45 * 86400
SCORE_DAYS = 30
SCORE_EVERY_S = 3600.0
TOP = 200
MIN_TOKEN_NEAR = 0.1  # a token with a smaller entry is not scored
MIN_TOKENS, MIN_INVESTED, MIN_WINS = 5, 3.0, 3
MIN_WIN_RATE, MIN_PNL, MIN_ROI, MAX_ONE_SHARE = 0.4, 3.0, 0.25, 0.8
PROXY_SIGNERS = 3  # an actor with that many different signers is a contract: the wallet is the signer
PENDING_MAX_BLOCKS = 60
CONTRACTS = frozenset({DCL, REF, INTEAR_ENGINE, WNEAR, NEARLY, "v2-launchpad.justhoot.near", "launchpad.justhoot.near",
                       "launch-v2.intear.near", "aggregatedex.near", "intents.near"})
CONTRACT_SUFFIXES = (".nearlytrade.near", ".justhoot.near", ".intear.near", ".ref-labs.near", ".ref-finance.near",
                     ".factory.bridge.near", ".omft.near")


@dataclass
class Trade:
    wallet: str
    token: str
    side: str  # buy | sell
    near: int  # yocto NEAR
    amount: int  # raw token units
    ts: float
    tx: str


@dataclass
class _Pending:
    block: int
    ts: float
    last: int
    legs: list[Leg] = field(default_factory=list)


def is_contract(account: str | None) -> bool:
    return not account or account in CONTRACTS or account.endswith(CONTRACT_SUFFIXES)


def trade_of(wallet: str, tx: str, ts: float, legs: list[Leg]) -> Trade | None:
    """Net flow of a transaction -> a NEAR <-> one token trade (or None: a stablecoin swap, several tokens)."""
    got, gave = net_flow(legs)
    got = [(t, a) for t, a in got if a > 0]
    gave = [(t, a) for t, a in gave if a > 0]
    if len(got) != 1 or len(gave) != 1:
        return None
    (t_in, a_in), (t_out, a_out) = got[0], gave[0]
    if t_out == NEAR_ASSET and t_in not in QUOTES:
        return Trade(wallet, t_in, "buy", a_out, a_in, ts, tx)
    if t_in == NEAR_ASSET and t_out not in QUOTES:
        return Trade(wallet, t_out, "sell", a_in, a_out, ts, tx)
    return None


class SmartTracker:
    """Legs of a transaction accumulate until the route ends (SETTLE_BLOCKS after the last one), then - a trade."""

    def __init__(self) -> None:
        self._pending: dict[tuple[str, str], _Pending] = {}
        self._signers: dict[str, set[str]] = {}  # actor -> signers (a contract is recognised by their variety)

    def proxy(self, actor: str) -> bool:
        return len(self._signers.get(actor, ())) >= PROXY_SIGNERS

    def parse(self, block: dict) -> list[Trade]:
        height, ts = header(block)
        for o in outcomes(block):
            if o.executor not in VENUES:
                continue
            try:
                legs = swap_legs(o.executor, o.receipt, o.outcome)
            except (KeyError, TypeError, ValueError):
                continue
            if not legs:
                continue
            tx_signer = signer(o.receipt)
            tx = o.tx_hash or o.receipt_id or ""
            for actor, leg in legs:
                if actor and tx_signer and actor != tx_signer:
                    seen = self._signers.setdefault(actor, set())
                    if len(seen) < PROXY_SIGNERS:
                        seen.add(tx_signer)
                wallet = tx_signer if (not actor or self.proxy(actor)) else actor
                if not wallet:
                    continue
                p = self._pending.setdefault((wallet, tx), _Pending(height, ts, height))
                p.legs.append(leg)
                p.last = height
        if len(self._signers) > 50_000:
            self._signers = {a: s for a, s in self._signers.items() if len(s) >= PROXY_SIGNERS}
        return self._flush(height)

    def _flush(self, height: int) -> list[Trade]:
        out = []
        for key, p in list(self._pending.items()):
            if height - p.last < SETTLE_BLOCKS and height - p.block < PENDING_MAX_BLOCKS:
                continue
            self._pending.pop(key)
            t = trade_of(key[0], key[1], p.ts, p.legs)
            if t is not None:
                out.append(t)
        return out


# ── scoring ─────────────────────────────────────────────────────────────────

def token_pnl(f: dict, price: float | None) -> float | None:
    """A wallet's profit in a token, NEAR: the matched part + the rest at the price. None - the entry is below MIN_TOKEN_NEAR."""
    near_in, near_out = float(f.get("near_in") or 0), float(f.get("near_out") or 0)
    tok_in, tok_out = float(f.get("tok_in") or 0), float(f.get("tok_out") or 0)
    if near_in < MIN_TOKEN_NEAR or tok_in <= 0:
        return None
    m = min(tok_in, tok_out)
    realized = (near_out * m / tok_out if tok_out else 0.0) - near_in * m / tok_in
    left = tok_in - m
    held = left * (price or 0.0) / NEAR - near_in * left / tok_in  # price - yocto per raw unit
    return realized + held


@dataclass
class Stat:
    wallet: str
    pnl: float = 0.0
    invested: float = 0.0
    tokens: int = 0
    wins: int = 0
    best: float = 0.0
    rank: int = 0

    @property
    def roi(self) -> float:
        return self.pnl / self.invested if self.invested else 0.0

    @property
    def win_rate(self) -> float:
        return self.wins / self.tokens if self.tokens else 0.0

    def qualified(self) -> bool:
        return (self.tokens >= MIN_TOKENS and self.invested >= MIN_INVESTED and self.wins >= MIN_WINS
                and self.win_rate >= MIN_WIN_RATE and self.pnl >= MIN_PNL and self.roi >= MIN_ROI
                and self.best <= self.pnl * MAX_ONE_SHARE)

    def doc(self) -> dict:
        return {"_id": self.wallet, "pnl": round(self.pnl, 4), "invested": round(self.invested, 4),
                "tokens": self.tokens, "wins": self.wins, "roi": round(self.roi, 4),
                "win_rate": round(self.win_rate, 4), "rank": self.rank}

    @classmethod
    def from_doc(cls, d: dict) -> Stat:
        return cls(d["_id"], float(d.get("pnl") or 0), float(d.get("invested") or 0), int(d.get("tokens") or 0),
                   int(d.get("wins") or 0), 0.0, int(d.get("rank") or 0))


def score(flows, prices: dict[str, float]) -> list[Stat]:
    """Flows (wallet, token) -> the "smart" ones by descending profit (at most TOP)."""
    by: dict[str, Stat] = {}
    for f in flows:
        pnl = token_pnl(f, prices.get(f["token"]))
        if pnl is None:
            continue
        s = by.setdefault(f["wallet"], Stat(f["wallet"]))
        s.pnl += pnl
        s.invested += float(f.get("near_in") or 0)
        s.tokens += 1
        s.wins += pnl > 0
        s.best = max(s.best, pnl)
    out = sorted((s for s in by.values() if s.qualified()), key=lambda s: s.pnl, reverse=True)[:TOP]
    for i, s in enumerate(out, 1):
        s.rank = i
    return out


class SmartMoney:
    """One per process: parses the stream, writes the flows, scores hourly. creator(token) -> its creator or None."""

    def __init__(self, db, creator=None) -> None:
        self.db = db
        self.flows = db["sniper_smart_flows"]
        self.prices_col = db["sniper_smart_prices"]
        self.wallets_col = db["sniper_smart_wallets"]
        self.tracker = SmartTracker()
        self.creator = creator or (lambda t: None)
        self.prices: dict[str, float] = {}  # token -> yocto NEAR per raw unit (last swap)
        self._tasks: set[asyncio.Task] = set()
        self.scored_at = 0.0

    async def ensure_indexes(self) -> None:
        await self.flows.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0)
        await self.flows.create_index([("wallet", ASCENDING)])
        await self.flows.create_index([("last_at", DESCENDING)])
        await self.wallets_col.create_index([("rank", ASCENDING)])

    async def rescore(self) -> list[Stat]:
        """Score over SCORE_DAYS: flows and prices from Mongo -> sniper_smart_wallets."""
        since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=SCORE_DAYS)
        prices = {d["_id"]: float(d.get("price") or 0) async for d in self.prices_col.find({}, {"price": 1})}
        prices |= self.prices
        flows = self.flows.find({"last_at": {"$gte": since}},
                                {"wallet": 1, "token": 1, "near_in": 1, "near_out": 1, "tok_in": 1, "tok_out": 1})
        stats = score([f async for f in flows], prices)
        run_at = dt.datetime.now(dt.timezone.utc)
        if stats:
            await self.wallets_col.bulk_write([UpdateOne({"_id": s.wallet}, {"$set": {**s.doc(), "at": run_at}},
                                                         upsert=True) for s in stats], ordered=False)
        await self.wallets_col.delete_many({"$or": [{"at": {"$lt": run_at}}, {"at": {"$exists": False}}]})
        self.scored_at = time.time()
        logger.info("smart money: %s wallets", len(stats))
        return stats

    async def run(self) -> None:
        await self.ensure_indexes()
        while True:
            try:
                await self.rescore()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("smart money: scoring failed")
            await asyncio.sleep(SCORE_EVERY_S)

    def _skip(self, t: Trade) -> bool:
        return (is_contract(t.wallet) or t.token in QUOTES or is_contract_token(t.token)
                or self.creator(t.token) == t.wallet)

    async def handle(self, block: dict) -> None:
        """Block stream subscriber: parses in place, writes in a background task - the stream never waits for Mongo."""
        trades = [t for t in self.tracker.parse(block) if not self._skip(t)]
        if not trades:
            return
        for t in trades:
            if t.amount > 0:
                self.prices[t.token] = t.near / t.amount
        task = asyncio.ensure_future(self._guard(self._save(trades)))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _guard(self, coro) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a failed batch must not go silent
            logger.exception("smart money: flows not written")

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _save(self, trades: list[Trade]) -> None:
        at = dt.datetime.now(dt.timezone.utc)
        expires = at + dt.timedelta(seconds=FLOWS_TTL_S)
        ops = []
        for t in trades:
            near = t.near / NEAR
            inc = ({"near_in": near, "tok_in": float(t.amount), "buys": 1} if t.side == "buy"
                   else {"near_out": near, "tok_out": float(t.amount), "sells": 1})
            ops.append(UpdateOne({"_id": f"{t.wallet}:{t.token}"},
                                 {"$inc": inc, "$min": {"first_at": at}, "$max": {"last_at": at},
                                  "$set": {"wallet": t.wallet, "token": t.token, "expires_at": expires}}, upsert=True))
        prices = {t.token: self.prices[t.token] for t in trades if t.token in self.prices}
        ops_p = [UpdateOne({"_id": tok}, {"$set": {"price": p, "at": at}}, upsert=True) for tok, p in prices.items()]
        await self.flows.bulk_write(ops, ordered=False)
        if ops_p:
            await self.prices_col.bulk_write(ops_p, ordered=False)


def is_contract_token(token: str) -> bool:
    return token in (WNEAR, NEAR_ASSET)
