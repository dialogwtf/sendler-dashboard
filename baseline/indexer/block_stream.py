"""Поток оптимистичных блоков neardata — один на процесс, для всех: индексатора (через кэш общего потока,
utils/block_cache.py, docs/SHARED_BLOCKS.md) и подписчиков (лента запусков снайпера).

Работает, только пока есть подписчик (снайпер держит lease). Без снайпера поток не нужен и индексатору: тот и так
качает каждый блок один раз, а поток ради одного индексатора стоил бы столько же или больше. Экономия возникает,
когда снайпер и индексатор в одном процессе: блок скачивается один раз — потоком, индексатор берёт его из кэша.
Нет подписчиков — ни одного запроса; появились — начинает с вершины.

Режим «next» (основной): запрашиваем сразу следующий блок /block_opt/{h+1}; сервер, если держит запрос до выхода
блока, отдаёт его сразу — один запрос на блок. null — высота пропущена (у NEAR бывают высоты без блока) или блока ещё
нет: это различает один /last_block/optimistic. Если сервер не держит запрос (FAST_MISS_LIMIT мгновенных «нет блока»
подряд), поток переходит в режим «poll»: опрос /last_block/optimistic раз в POLL_S, недостающие блоки — по номеру.
Отставание больше MAX_GAP — прыжок к вершине (поток нужен живой; полноту индексатору даёт его собственное скачивание
всего, что не доказано кэшем).

Подписчик — async-функция block → None; вызывается по порядку блоков, его ошибка не останавливает поток.
"""
from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

from utils import block_cache

logger = logging.getLogger("block_stream")

MAX_GAP = 30
POLL_S = 0.25
IDLE_S = 1.0  # проверка «нужен ли поток», пока он никому не нужен
NEXT_TIMEOUT_S = 8.0  # сколько держим запрос следующего блока
FAST_MISS_S = 0.15  # «блока нет» быстрее — сервер не ждал блок
FAST_MISS_LIMIT = 8  # столько мгновенных «нет» подряд — переходим на опрос
HEAD_CHECK_EVERY = 100  # в режиме «next» сверяться с вершиной раз в 100 блоков

STREAM: BlockStream | None = None  # поток процесса (main.py); None — снайпер запускает свой


def _metrics(name: str, value: float, gauge: bool = True) -> None:
    try:
        from health.server import inc, set_gauge

        (set_gauge if gauge else inc)(name, value)
    except Exception:  # noqa: BLE001
        pass


class BlockStream:
    def __init__(self, base_url: str, api_key: str | None, session: aiohttp.ClientSession) -> None:
        self.base = base_url.rstrip("/")
        self.key = api_key
        self.session = session
        self.height = 0
        self.last_ok = 0.0
        self.mode = "next"
        self._subs: list = []
        self._fast_misses = 0  # подряд мгновенных «блока ещё нет» на вершине (сервер не держит запрос)
        self._since_head = 0  # блоков с последней сверки с вершиной
        self._stopped = False

    # ── подписка ────────────────────────────────────────────────────────────

    def subscribe(self, handler):
        """handler(block) — async. → функция отписки."""
        self._subs.append(handler)

        def unsubscribe() -> None:
            if handler in self._subs:
                self._subs.remove(handler)

        return unsubscribe

    def demanded(self) -> bool:
        return bool(self._subs)

    def stop(self) -> None:
        self._stopped = True

    # ── сеть ────────────────────────────────────────────────────────────────

    async def _get(self, path: str, missing_ok: bool = False, timeout: float = 8) -> dict | None:
        url = f"{self.base}{path}" + (f"?apiKey={self.key}" if self.key else "")
        async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            if missing_ok and 400 <= r.status < 500 and r.status != 429:
                return None  # блока нет (ещё не вышел или высота пропущена)
            if r.status != 200:
                raise RuntimeError(f"neardata {path}: HTTP {r.status}")
            return await r.json(content_type=None)

    async def _top(self) -> tuple[int, dict]:
        head = await self._get("/last_block/optimistic")
        return int(head["block"]["header"]["height"]), head

    async def _emit(self, block: dict) -> None:
        block_cache.offer(block)  # общий поток: индексатор возьмёт блок отсюда, когда тот станет финальным
        _metrics("block_stream_height", self.height)
        _metrics("block_cache_blocks", len(block_cache.BLOCKS))
        _metrics("block_stream_blocks_total", 1, gauge=False)
        for handler in list(self._subs):
            try:
                await handler(block)
            except Exception:  # noqa: BLE001 — подписчик не должен останавливать поток
                logger.exception("block_stream: подписчик упал на блоке %s", self.height)

    # ── цикл ────────────────────────────────────────────────────────────────

    async def run(self) -> None:
        errors = 0
        while not self._stopped:
            if not self.demanded():
                self.height = 0  # понадобится снова — начнём с вершины
                await asyncio.sleep(IDLE_S)
                continue
            try:
                if self.mode == "next" and self.height:
                    await self._step_next()
                else:
                    await self._step_poll()
                self.last_ok = time.time()
                errors = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                errors += 1
                if errors in (1, 10) or errors % 100 == 0:
                    logger.warning("block_stream: ошибка (%s подряд): %s", errors, str(e)[:200])
                await asyncio.sleep(min(5.0, 0.5 * errors))

    async def _step_next(self) -> None:
        """Один шаг режима «next»: следующий блок по номеру."""
        h = self.height + 1
        t0 = time.monotonic()
        block = await self._get(f"/block_opt/{h}", missing_ok=True, timeout=NEXT_TIMEOUT_S)
        if isinstance(block, dict) and block.get("block"):
            self.height = h
            self._fast_misses = 0
            await self._emit(block)
            self._since_head += 1
            if self._since_head >= HEAD_CHECK_EVERY:  # иногда сверяемся с вершиной: не отстали ли
                self._since_head = 0
                top, _ = await self._top()
                if top - self.height > MAX_GAP:
                    self.height = top - 1
            return
        fast = time.monotonic() - t0 < FAST_MISS_S
        top, head = await self._top()
        self._since_head = 0
        if top - self.height > MAX_GAP:
            self.height = top - 1
        elif top > h:
            self.height = h  # высота пропущена — дальше
        elif top == h:
            self.height = h  # блок уже есть, а по номеру не отдали — берём вершину
            await self._emit(head)
        else:  # блока ещё нет
            self._fast_misses = self._fast_misses + 1 if fast else 0
            if self._fast_misses >= FAST_MISS_LIMIT:
                self.mode = "poll"
                _metrics("block_stream_poll_mode", 1)
                logger.warning("block_stream: neardata не держит запрос следующего блока — перехожу на опрос")
            await asyncio.sleep(POLL_S)

    async def _step_poll(self) -> None:
        """Опрос вершины, недостающие блоки — по номеру (старт и запасной режим)."""
        top, head = await self._top()
        if not self.height or top - self.height > MAX_GAP:
            self.height = top - 1
        while self.height < top and not self._stopped:
            h = self.height + 1
            block = head if h == top else await self._get(f"/block_opt/{h}")
            self.height = h
            if block:
                await self._emit(block)
        if self.mode == "poll":
            await asyncio.sleep(POLL_S)
