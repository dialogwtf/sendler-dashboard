"""Environment variables. Loaded before core/app."""
import os
from typing import Tuple

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# MongoDB: the database the indexer writes to (its MONGO_DB_NAME)
MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAMES: Tuple[str, ...] = ("near_indexer",)

# NEAR RPC, comma-separated for failover. Used once per wallet sign-in (view_access_key), nowhere else.
NEAR_RPC_URLS: list = [u.strip() for u in os.getenv("NEAR_RPC_URL", "https://rpc.mainnet.near.org").split(",")
                       if u.strip()]

# Optional API keys (comma-separated, header X-API-Key). The dashboard routes stay public unless
# DASH_REQUIRE_API_KEY=1 (config/dash.py); empty = no key check at all.
ALLOWED_API_KEYS: str = os.getenv("ALLOWED_API_KEYS", "")

# Reverse proxies (IPs/CIDRs, comma-separated) whose client-IP headers are trusted for rate limiting:
# CF-Connecting-IP, X-Real-IP, X-Forwarded-For. Empty = limit by the TCP peer.
TRUSTED_PROXY_IPS: str = os.getenv("TRUSTED_PROXY_IPS", "")

# Browser origins allowed by CORS, comma-separated
CORS_ORIGINS: list = [o.strip() for o in os.getenv(
    "CORS_ORIGINS", "https://dash.sendler.xyz,http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()]

LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
