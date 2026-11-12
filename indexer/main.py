"""Sendler Dash indexer: one optimistic block stream of NEAR mainnet -> MongoDB `dash_*` collections.

Tasks of the process:
- BlockStream (indexer/block_stream.py) - downloads every optimistic block from neardata once;
- DashWorker (services/dashboard/worker.py) - trades, candles, launches, big moves, network metrics, new contracts
  and pools, holders, the live event feed for the API's WebSocket;
- SmartMoney (services/dashboard/smart.py) - per-wallet flows and the hourly ranking of profitable wallets;
- LaunchStats (services/dashboard/launch_stats.py) - what the creator did with his own tokens in each launch;
- FtPrices / TokenMeta (services/dashboard/reference.py) - USD rates from REF Finance, token metadata (NEP-148);
- health server - /health and /metrics (Prometheus text).

Everything on the block path is derived from the blocks themselves; the only RPC call is one `ft_metadata` view
per new token, in the background.
"""
from __future__ import annotations

import asyncio
import logging
import signal

import aiohttp

from config import Settings
from database.db import get_db
from health.server import run_health_server
from indexer import block_stream
from indexer.block_stream import BlockStream
from services.dashboard.launch_stats import LaunchStats
from services.dashboard.reference import FtPrices, TokenMeta
from services.dashboard.smart import SmartMoney
from services.dashboard.worker import DashWorker

logger = logging.getLogger("main")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db = get_db()
    async with aiohttp.ClientSession() as session:
        stream = BlockStream(Settings.NEAR_DATA_API, Settings.NEAR_DATA_API_KEY, session)
        block_stream.STREAM = stream
        worker = DashWorker(db=db, stream=stream)
        tasks = [asyncio.create_task(stream.run(), name="block_stream"),
                 asyncio.create_task(worker.run(), name="dashboard"),
                 asyncio.create_task(run_health_server(db, port=Settings.HEALTH_PORT), name="health")]
        if Settings.DASH_ENABLED and Settings.DASH_SMART_MONEY:
            # the creator of a launch token is not "smart money" in his own token
            smart = SmartMoney(db, creator=worker.launches.creator_of)
            stream.subscribe(smart.handle)
            tasks.append(asyncio.create_task(smart.run(), name="smart_money"))
        if Settings.DASH_ENABLED and Settings.DASH_LAUNCH_STATS:
            stats = LaunchStats(db)
            await stats.start()
            stream.subscribe(stats.handle)
        if Settings.DASH_ENABLED and Settings.DASH_REFERENCE:
            tasks.append(asyncio.create_task(FtPrices(db, session, Settings.REF_PRICES_URL).run(), name="ft_prices"))
            tasks.append(asyncio.create_task(TokenMeta(db, session, Settings.NEAR_RPC_URL).run(), name="token_meta"))

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # Windows: Ctrl+C raises KeyboardInterrupt instead
                pass
        waiter = asyncio.create_task(stop.wait())
        try:
            done, _ = await asyncio.wait([waiter, *tasks], return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                if t is not waiter and not t.cancelled() and t.exception():
                    logger.error("task %s failed: %r", t.get_name(), t.exception())
        finally:
            worker.stop()
            stream.stop()
            for t in (waiter, *tasks):
                t.cancel()
            await asyncio.gather(waiter, *tasks, return_exceptions=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
