"""Sendler Dash - public dashboard of NEAR memes and network. Everything is derived from the block stream, no RPC.

- worker.py - DashWorker: block stream subscriber, writes the dash_* collections (data contract: docs/DASH_DATA.md);
- trades.py - trades and candles from swaps; prices.py - USD rates from the stream;
- feed.py / launches.py / fee_farm.py - launchpad launches (Nearly, JustHoot, Intear) and their stages;
- holders.py - balances of launch tokens from NEP-141 events;
- blocks.py / network.py - big transfers, new contracts / tokens / pools, network metrics (HyperLogLog signers);
- smart.py - smart money: per-wallet flows and the hourly ranking;
- launch_stats.py - what each creator did with his own tokens; reference.py - USD rates and token metadata;
- events.py - capped event feed for the API's WebSocket, account labels;
- history.py - history before the live start (scripts/backfill_dash.py).
"""
