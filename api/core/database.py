"""MongoDB connection."""
import logging
from motor.motor_asyncio import AsyncIOMotorClient

from config.env import MONGO_URI, DB_NAMES

logger = logging.getLogger(__name__)


class Database:
    _client = None
    _databases = {}

    @classmethod
    def init(cls, uri: str = None, db_names: tuple = None):
        """Initialize MongoDB connection."""
        if cls._client is None:
            cls._client = AsyncIOMotorClient(uri or MONGO_URI)
            names = db_names or DB_NAMES
            for db_name in names:
                cls._databases[db_name] = cls._client[db_name]
            logger.info(f"MongoDB client initialized with databases: {', '.join(names)}")

    @classmethod
    def get_db(cls, db_name: str = "blockchain_db"):
        """Return connection to the specified database."""
        if db_name not in cls._databases:
            raise RuntimeError(
                f"Database '{db_name}' is not initialized. Call Database.init() first."
            )
        return cls._databases[db_name]

    @classmethod
    async def close(cls):
        """Close MongoDB connection."""
        if cls._client:
            cls._client.close()
            logger.info("MongoDB connection closed.")
            cls._client = None
            cls._databases = {}
