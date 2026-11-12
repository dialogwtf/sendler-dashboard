"""
Лёгкий HTTP сервер для health check и Prometheus метрик.

Эндпоинты:
  GET /health   — проверяет подключение к MongoDB
  GET /metrics  — метрики в формате Prometheus text

Запускается как фоновая coroutine рядом с основными воркерами.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Метрики (простые счётчики/gauge без внешних зависимостей)
# ---------------------------------------------------------------------------

_metrics: dict[str, float] = {
    "blocks_processed_total": 0,
    "receipts_parsed_total": 0,
    "receipts_skipped_total": 0,
    "receipts_error_total": 0,
    "start_time_seconds": time.time(),
}


def inc(name: str, value: float = 1.0) -> None:
    """Увеличить счётчик метрики."""
    _metrics[name] = _metrics.get(name, 0) + value


_gauges: set[str] = {"start_time_seconds"}


def set_gauge(name: str, value: float) -> None:
    """Установить значение gauge метрики."""
    _gauges.add(name)
    _metrics[name] = value


def set_gauge_max(name: str, value: float) -> None:
    """Gauge, который только растёт: несколько воркеров пишут его вперемешку."""
    _gauges.add(name)
    if value > _metrics.get(name, float("-inf")):
        _metrics[name] = value


# ---------------------------------------------------------------------------
# Обработчики
# ---------------------------------------------------------------------------

async def _health_handler(request: web.Request) -> web.Response:
    db: AsyncIOMotorDatabase = request.app["mongo_db"]
    checks: dict[str, str] = {}
    ok = True

    # MongoDB
    try:
        await asyncio.wait_for(db.command("ping"), timeout=3)
        checks["mongo"] = "ok"
    except Exception as e:
        checks["mongo"] = f"error: {e}"
        ok = False

    status = 200 if ok else 503
    return web.json_response({"status": "ok" if ok else "degraded", "checks": checks}, status=status)


async def _metrics_handler(request: web.Request) -> web.Response:
    uptime = time.time() - _metrics.get("start_time_seconds", time.time())

    lines = [
        "# HELP indexer_uptime_seconds Uptime in seconds",
        "# TYPE indexer_uptime_seconds gauge",
        f"indexer_uptime_seconds {uptime:.1f}",
    ]

    for name, value in _metrics.items():
        if name == "start_time_seconds":
            continue
        metric_type = "gauge" if name in _gauges else "counter"
        full_name = f"indexer_{name}"
        lines += [
            f"# TYPE {full_name} {metric_type}",
            f"{full_name} {value}",
        ]

    body = "\n".join(lines) + "\n"
    return web.Response(text=body, content_type="text/plain")


# ---------------------------------------------------------------------------
# Запуск сервера
# ---------------------------------------------------------------------------

async def run_health_server(
    mongo_db,
    host: str = "0.0.0.0",
    port: int = 8080,
) -> None:
    """Запускает health/metrics сервер как долгоживущую coroutine."""
    app = web.Application()
    app["mongo_db"] = mongo_db

    app.router.add_get("/health", _health_handler)
    app.router.add_get("/metrics", _metrics_handler)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("🩺 Health server запущен на http://%s:%d", host, port)

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()
