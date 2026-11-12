"""MongoDB connection (Motor). The dashboard worker creates its own indexes on start."""
from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorClient

from config import Settings

_client: AsyncIOMotorClient | None = None


def get_db():
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(Settings.MONGO_URI, tz_aware=True, maxPoolSize=50)
    return _client[Settings.MONGO_DB_NAME]
