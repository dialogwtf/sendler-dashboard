"""Stream of optimistic blocks from neardata - one per process, for every subscriber (the dashboard worker, smart money).

Runs only while somebody is subscribed: no subscribers - not a single request; they appear - it starts from the head.

"next" mode (the main one): ask for the next block right away, /block_opt/{h+1}; a server that holds the request
until the block is produced answers at once - one request per block. null means the height was skipped (NEAR has
heights without a block) or the block is not there yet: one /last_block/optimistic tells them apart. If the server
does not hold the request (FAST_MISS_LIMIT instant "no block" answers in a row), the stream switches to "poll" mode:
/last_block/optimistic every POLL_S, missing blocks by number. Falling behind by more than MAX_GAP - jump to the head
(the stream has to stay live; the dashboard worker refetches the gap itself by `prev_height`).

A subscriber is an async function block -> None; called in block order, its error does not stop the stream.
"""
from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

logger = logging.getLogger("block_stream")

MAX_GAP = 30
POLL_S = 0.25
IDLE_S = 1.0  # the "is the stream needed" check while nobody needs it
NEXT_TIMEOUT_S = 8.0  # how long the request for the next block is held
FAST_MISS_S = 0.15  # "no block" faster than this - the server did not wait for the block
FAST_MISS_LIMIT = 8  # that many instant "no" answers in a row - switch to polling
HEAD_CHECK_EVERY = 100  # in "next" mode compare with the head once per 100 blocks

STREAM: BlockStream | None = None  # the process's stream (main.py)


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
        self._fast_misses = 0  # instant "no block yet" answers in a row at the head (the server does not hold the request)
        self._since_head = 0  # blocks since the last comparison with the head
        self._stopped = False

    # ── subscription ────────────────────────────────────────────────────────

    def subscribe(self, handler):
        """handler(block) - async. Returns the unsubscribe function."""
        self._subs.append(handler)

        def unsubscribe() -> None:
            if handler in self._subs:
                self._subs.remove(handler)

        return unsubscribe

    def demanded(self) -> bool:
        return bool(self._subs)

    def stop(self) -> None:
        self._stopped = True

    # ── network ─────────────────────────────────────────────────────────────

    async def _get(self, path: str, missing_ok: bool = False, timeout: float = 8) -> dict | None:
        url = f"{self.base}{path}" + (f"?apiKey={self.key}" if self.key else "")
        async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            if missing_ok and 400 <= r.status < 500 and r.status != 429:
                return None  # no block (not produced yet, or the height was skipped)
            if r.status != 200:
                raise RuntimeError(f"neardata {path}: HTTP {r.status}")
            return await r.json(content_type=None)

    async def _top(self) -> tuple[int, dict]:
        head = await self._get("/last_block/optimistic")
        return int(head["block"]["header"]["height"]), head

    async def _emit(self, block: dict) -> None:
        _metrics("block_stream_height", self.height)
        _metrics("block_stream_blocks_total", 1, gauge=False)
        for handler in list(self._subs):
            try:
                await handler(block)
            except Exception:  # noqa: BLE001 - a subscriber must not stop the stream
                logger.exception("block_stream: subscriber failed on block %s", self.height)

    # ── loop ────────────────────────────────────────────────────────────────

    async def run(self) -> None:
        errors = 0
        while not self._stopped:
            if not self.demanded():
                self.height = 0  # needed again - start from the head
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
                    logger.warning("block_stream: error (%s in a row): %s", errors, str(e)[:200])
                await asyncio.sleep(min(5.0, 0.5 * errors))

    async def _step_next(self) -> None:
        """One step of "next" mode: the next block by number."""
        h = self.height + 1
        t0 = time.monotonic()
        block = await self._get(f"/block_opt/{h}", missing_ok=True, timeout=NEXT_TIMEOUT_S)
        if isinstance(block, dict) and block.get("block"):
            self.height = h
            self._fast_misses = 0
            await self._emit(block)
            self._since_head += 1
            if self._since_head >= HEAD_CHECK_EVERY:  # compare with the head now and then: have we fallen behind
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
            self.height = h  # the height was skipped - move on
        elif top == h:
            self.height = h  # the block exists but was not served by number - take the head
            await self._emit(head)
        else:  # no block yet
            self._fast_misses = self._fast_misses + 1 if fast else 0
            if self._fast_misses >= FAST_MISS_LIMIT:
                self.mode = "poll"
                _metrics("block_stream_poll_mode", 1)
                logger.warning("block_stream: neardata does not hold the next-block request - switching to polling")
            await asyncio.sleep(POLL_S)

    async def _step_poll(self) -> None:
        """Poll the head, missing blocks by number (start and fallback mode)."""
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
