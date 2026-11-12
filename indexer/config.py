"""Settings of the Sendler Dash indexer: environment variables, `.env` in the working directory.

Access as `Settings.MONGO_URI` (values are read on every access, so tests and a reloaded .env see changes).
"""
from __future__ import annotations

import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional: plain environment variables work too
    pass


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None or not raw.strip() else raw.strip().lower() in ("1", "true", "yes", "on")


class _Settings:
    @property
    def MONGO_URI(self) -> str:
        return os.getenv("MONGO_URI", "mongodb://localhost:27017")

    @property
    def MONGO_DB_NAME(self) -> str:
        return os.getenv("MONGO_DB_NAME", "near_indexer")

    @property
    def NEAR_DATA_API(self) -> str:
        """neardata block API: /last_block/optimistic, /block_opt/{height}, /block/{height}."""
        return os.getenv("NEAR_DATA_API", "https://mainnet.neardata.xyz/v0")

    @property
    def NEAR_DATA_API_KEY(self) -> str | None:
        return os.getenv("NEAR_DATA_API_KEY") or None

    @property
    def NEAR_RPC_URL(self) -> str:
        """Used only for token metadata (`ft_metadata` view, once per token) - never on the block path."""
        return os.getenv("NEAR_RPC_URL", "https://free.rpc.fastnear.com")

    @property
    def REF_PRICES_URL(self) -> str:
        return os.getenv("REF_PRICES_URL", "https://api.ref.finance/list-token-price")

    @property
    def HEALTH_PORT(self) -> int:
        return int(os.getenv("HEALTH_PORT", "8080"))

    @property
    def DASH_ENABLED(self) -> bool:
        return _bool("DASH_ENABLED", True)

    @property
    def DASH_SMART_MONEY(self) -> bool:
        """Smart money (services/dashboard/smart.py): flows per wallet and the hourly ranking."""
        return _bool("DASH_SMART_MONEY", True)

    @property
    def DASH_LAUNCH_STATS(self) -> bool:
        """What the creator did in each launch (services/dashboard/launch_stats.py) - the creator dossier."""
        return _bool("DASH_LAUNCH_STATS", True)

    @property
    def DASH_REFERENCE(self) -> bool:
        """USD rates (REF Finance) and token metadata (services/dashboard/reference.py)."""
        return _bool("DASH_REFERENCE", True)

    @property
    def DASH_QUEUE(self) -> int:
        """Blocks waiting for the worker; when full a block is dropped and refetched as a gap."""
        return max(10, int(os.getenv("DASH_QUEUE", "2000")))

    @property
    def DASH_GAP_MAX(self) -> int:
        """A gap longer than this many heights is not refetched (~6 min)."""
        return max(0, int(os.getenv("DASH_GAP_MAX", "600")))

    @property
    def DASH_TRADE_MIN_NEAR(self) -> float:
        """Smaller trades are not written to dash_trades (they still count in candles)."""
        return float(os.getenv("DASH_TRADE_MIN_NEAR", "0.05"))

    @property
    def DASH_TRADES_TTL_DAYS(self) -> int:
        return max(1, int(os.getenv("DASH_TRADES_TTL_DAYS", "7")))


Settings = _Settings()
