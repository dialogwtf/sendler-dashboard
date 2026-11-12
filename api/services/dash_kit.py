"""Sendler Dash helpers: single-flight TTL cache and JSON for Mongo docs."""
from __future__ import annotations

import asyncio
import datetime as dt
import math
import time

from bson import ObjectId


def jsonable(v):
    """Mongo doc → JSON: dates as ISO UTC, ObjectId as string, bytes dropped, NaN/inf → null."""
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items() if not isinstance(x, (bytes, bytearray))}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if isinstance(v, dt.datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=dt.timezone.utc)
        return v.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(v, ObjectId):
        return str(v)
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if hasattr(v, "to_decimal"):  # Decimal128
        return float(v.to_decimal())
    return v


class TTLCache:
    """Value per key for ttl seconds; concurrent misses of one key wait for a single computation.
    Errors are not cached."""

    def __init__(self, max_keys: int = 5000) -> None:
        self._data: dict[str, tuple[float, object]] = {}
        self._inflight: dict[str, asyncio.Future] = {}
        self._max = max_keys

    async def get(self, key: str, ttl: float, make):
        now = time.monotonic()
        hit = self._data.get(key)
        if hit and hit[0] > now:
            return hit[1]
        fut = self._inflight.get(key)
        if fut:
            return await asyncio.shield(fut)
        fut = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        try:
            value = await make()
        except BaseException as e:
            fut.set_exception(e)
            fut.exception()  # mark retrieved: no "exception never retrieved" when nobody waits
            raise
        else:
            fut.set_result(value)
            if ttl > 0:
                if len(self._data) >= self._max:
                    self._data = {k: v for k, v in self._data.items() if v[0] > now}
                    if len(self._data) >= self._max:
                        self._data.clear()
                self._data[key] = (now + ttl, value)
            return value
        finally:
            self._inflight.pop(key, None)

    def clear(self) -> None:
        self._data.clear()
