"""Статистика дева по каждому запуску Nearly и JustHoot (sniper_launch_stats) и сигнал «дев продаёт».

Основа умной блокировки девов (§23 плана): сколько создатель получил своих токенов (покупка на старте, комиссии),
сколько продал и когда, разослал ли другим кошелькам. Источник — общий поток блоков, без RPC:
- nep141 ft_transfer в квитанциях токена: создатель получил (new_owner_id) или отдал (old_owner_id); отдал в DCL —
  продажа, другому кошельку — раздача;
- dcl.ref swap со swapper = создатель и token_in = токен: продажа и её сумма в NEAR (пул с wNEAR);
- начало торгов (t0) — первый перевод токена из DCL покупателю.
Запуск попадает в наблюдение по событию started ленты (track), наблюдается WATCH_S после начала торгов; через
DUMP_WINDOW_S торгов фиксируется «слил» — продал ≥ DUMP_SHARE полученного (разбор 9–17.10: так в 15% запусков, и
наши автопокупки в них −3.99 NEAR при 13% выигрышей). Каждая продажа дева сразу уходит в on_dev_sell (выход из
позиции автопокупки — positions.py). Запись в Mongo — фоном, лента не ждёт. После рестарта наблюдение незакрытых
запусков продолжается по Mongo.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

from .store import now

logger = logging.getLogger("sniper")

DCL = "dclv2.ref-labs.near"
WNEAR = "wrap.near"
WATCH_S = 3600.0  # наблюдать запуск час от начала торгов (отчёты и правила по истории)
DUMP_WINDOW_S = 300.0  # «слил» — в первые 5 мин торгов
DUMP_SHARE = 0.5  # продал ≥ половины полученного
LAUNCHPADS = ("nearly", "justhoot")
TTL_S = 90 * 86400


def dumped(dev_in: int, sold_window: int) -> bool:
    return dev_in > 0 and sold_window >= dev_in * DUMP_SHARE


@dataclass
class Stat:
    token: str
    creator: str
    launchpad: str
    pool: str | None = None
    started_at: float = 0.0
    t0: float | None = None  # начало торгов (первый перевод из DCL)
    dev_in: int = 0  # токенов получено создателем
    dev_sold: int = 0  # продано в DCL
    dev_sold_near: float = 0.0  # NEAR за продажи (пулы с wNEAR)
    dev_out: int = 0  # отдано другим кошелькам
    sold_window: int = 0  # продано за первые DUMP_WINDOW_S торгов
    first_sell_at: float | None = None
    dumped: bool | None = None  # None — окно ещё не закрыто
    sells: int = 0
    seen: set = field(default_factory=set)  # receipt_id уже учтённых переводов (поток может повторить блок)

    def apply(self, old: str | None, new: str | None, amount: int, ts: float) -> bool:
        """Перевод токена запуска (из потока или из ft_transfers при загрузке истории). → изменилось ли."""
        changed = False
        if old == DCL and self.t0 is None and new != self.token:
            self.t0 = ts
            changed = True
        if new == self.creator:
            self.dev_in += amount
            return True
        if old == self.creator:
            if new == DCL:
                self.dev_sold += amount
                self.sells += 1
                self.first_sell_at = self.first_sell_at or ts
                if self.t0 is None or ts - self.t0 <= DUMP_WINDOW_S:
                    self.sold_window += amount
            else:
                self.dev_out += amount
            return True
        return changed

    def doc(self) -> dict:
        return {"_id": self.token, "creator": self.creator, "launchpad": self.launchpad, "pool": self.pool,
                "started_at": self.started_at, "t0": self.t0, "dev_in": str(self.dev_in),
                "dev_sold": str(self.dev_sold), "dev_sold_near": round(self.dev_sold_near, 6),
                "dev_out": str(self.dev_out), "sold_window": str(self.sold_window),
                "first_sell_at": self.first_sell_at,
                "first_sell_s": (self.first_sell_at - self.t0) if self.first_sell_at and self.t0 else None,
                "dumped": self.dumped, "sells": self.sells, "updated_at": now()}

    @classmethod
    def from_doc(cls, d: dict) -> Stat:
        return cls(d["_id"], d["creator"], d.get("launchpad") or "", d.get("pool"), float(d.get("started_at") or 0),
                   d.get("t0"), int(d.get("dev_in") or 0), int(d.get("dev_sold") or 0),
                   float(d.get("dev_sold_near") or 0), int(d.get("dev_out") or 0), int(d.get("sold_window") or 0),
                   d.get("first_sell_at"), d.get("dumped"), int(d.get("sells") or 0))


@dataclass
class DevSell:
    """Продажа создателем своего токена: amount — токенов, near — NEAR (None — пул не с wNEAR), share — доля от
    полученного им к этому моменту."""
    stat: Stat
    amount: int
    near: float | None
    share: float
    block: int
    ts: float


class LaunchStats:
    """on_dev_sell — async (DevSell) → None: вызывается фоном на каждую продажу дева."""

    def __init__(self, db, on_dev_sell=None) -> None:
        self.col = db["sniper_launch_stats"] if db is not None else None
        self.on_dev_sell = on_dev_sell
        self.active: dict[str, Stat] = {}
        self.background = True
        self._tasks: set[asyncio.Task] = set()

    async def ensure_indexes(self) -> None:
        await self.col.create_index("creator")
        await self.col.create_index("updated_at", expireAfterSeconds=TTL_S)

    async def load(self) -> None:
        """После рестарта: запуски, наблюдение за которыми не закончилось."""
        since = time.time() - WATCH_S
        async for d in self.col.find({"started_at": {"$gte": since}}):
            self.active[d["_id"]] = Stat.from_doc(d)

    def track(self, ev) -> None:
        """Запуск из ленты (started или ready, если started пропущен): начать наблюдение."""
        if ev.launchpad not in LAUNCHPADS or not ev.token or not ev.creator or ev.token in self.active:
            return
        pool = ev.pool if ev.pool and "|" in ev.pool else None
        self.active[ev.token] = Stat(ev.token, ev.creator, ev.launchpad, pool, ev.block_ts or time.time())
        self._save(self.active[ev.token])

    def stat(self, token: str) -> Stat | None:
        return self.active.get(token)

    async def handle(self, block: dict) -> None:
        """Подписчик потока блоков."""
        if not self.active:
            return
        header = (block.get("block") or {}).get("header") or {}
        height = int(header.get("height") or 0)
        ts = int(header.get("timestamp") or 0) / 1e9 or time.time()
        changed: set[str] = set()
        sells: list[DevSell] = []
        for shard in block.get("shards") or []:
            for reo in shard.get("receipt_execution_outcomes") or []:
                outcome = (reo.get("execution_outcome") or {}).get("outcome") or {}
                status = outcome.get("status") or {}
                if "SuccessValue" not in status and "SuccessReceiptId" not in status:
                    continue
                ex = outcome.get("executor_id") or (reo.get("receipt") or {}).get("receiver_id")
                rid = (reo.get("receipt") or {}).get("receipt_id") or (reo.get("execution_outcome") or {}).get("id")
                try:
                    if ex in self.active:
                        if self._transfers(self.active[ex], outcome.get("logs") or [], rid, ts):
                            changed.add(ex)
                    elif ex == DCL:
                        for s in self._swaps(outcome.get("logs") or [], height, ts):
                            sells.append(s)
                            changed.add(s.stat.token)
                except (KeyError, TypeError, ValueError) as e:
                    logger.debug("sniper launch stats: квитанция не разобрана: %s", e)
        for token in list(self.active):
            st = self.active[token]
            if st.dumped is None and st.t0 and ts - st.t0 >= DUMP_WINDOW_S:
                st.dumped = dumped(st.dev_in, st.sold_window)
                changed.add(token)
            if (st.t0 or st.started_at) and ts - (st.t0 or st.started_at) > WATCH_S:
                self._save(st)
                self.active.pop(token, None)
                changed.discard(token)
        for token in changed:
            self._save(self.active[token])
        for s in sells:
            if self.on_dev_sell is not None:
                await self._spawn(self.on_dev_sell(s))

    def _transfers(self, st: Stat, logs: list[str], rid, ts: float) -> bool:
        changed = False
        for ev in _events(logs):
            if ev.get("standard") != "nep141" or ev.get("event") != "ft_transfer":
                continue
            for i, d in enumerate(_items(ev.get("data"))):
                key = (rid, i)
                if rid and key in st.seen:
                    continue
                st.seen.add(key)
                changed |= st.apply(d.get("old_owner_id"), d.get("new_owner_id"), int(d.get("amount") or 0), ts)
        return changed

    def _swaps(self, logs: list[str], height: int, ts: float) -> list[DevSell]:
        out = []
        for ev in _events(logs):
            if ev.get("standard") != "dcl.ref" or ev.get("event") != "swap":
                continue
            for d in _items(ev.get("data")):
                st = self.active.get(d.get("token_in") or "")
                if st is None or d.get("swapper") != st.creator:
                    continue
                amount = int(d.get("amount_in") or 0)
                near = int(d.get("amount_out") or 0) / 1e24 if d.get("token_out") == WNEAR else None
                if near is not None:
                    st.dev_sold_near += near
                # доля — от полученного к моменту продажи (перевод в DCL этой же продажи уже мог быть учтён)
                share = amount / st.dev_in if st.dev_in else 1.0
                out.append(DevSell(st, amount, near, min(share, 1.0), height, ts))
        return out

    def _save(self, st: Stat) -> None:
        if self.col is None:
            return
        doc = st.doc()

        async def write():
            try:
                await self.col.update_one({"_id": st.token}, {"$set": doc}, upsert=True)
            except Exception as e:  # noqa: BLE001 — статистика не должна мешать ленте
                logger.warning("sniper: статистика запуска %s не записана: %s", st.token, e)

        if self.background:
            task = asyncio.ensure_future(write())
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _spawn(self, coro) -> None:
        if not self.background:
            await coro
            return
        task = asyncio.create_task(self._guard(coro))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @staticmethod
    async def _guard(coro) -> None:
        try:
            await coro
        except Exception:  # noqa: BLE001
            logger.exception("sniper: обработка продажи дева упала")

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


def _events(logs: list[str]) -> list[dict]:
    out = []
    for line in logs:
        if line.startswith("EVENT_JSON:"):
            try:
                out.append(json.loads(line[len("EVENT_JSON:"):]))
            except ValueError:
                pass
    return out


def _items(data) -> list[dict]:
    return [d for d in (data if isinstance(data, list) else [data]) if isinstance(d, dict)]
