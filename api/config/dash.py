"""Sendler Dash (/v2/dash) config — public dashboard dash.sendler.xyz.

Data is written by the indexer (`indexer/services/dashboard/worker.py`, data contract —
`indexer/docs/DASH_DATA.md`); this API only reads it and never calls RPC (except one call per wallet sign-in).
"""
import os
import re

import config.env  # noqa: F401 — loads .env before the getenv calls below


def _env(name: str, default: str) -> str:
    """Env value without a trailing ` # comment` (systemd EnvironmentFile keeps it)."""
    return re.sub(r"\s+#.*$", "", os.getenv(name, default)).strip()


# The dashboard is called from the browser without X-API-Key (like /v2/intents); 1 = back behind the key.
DASH_REQUIRE_API_KEY: bool = _env("DASH_REQUIRE_API_KEY", "0") in ("1", "true", "yes")

# Must match the indexer: services/dashboard/events.py EVENTS and launches.py STAGE_NAMES.
EVENTS_COLLECTION = "dash_events"
STAGE_NAMES = {1: "started", 2: "created", 3: "funding", 4: "live", 5: "ready"}

# The Sendler trade bot's shared auto-block of creators (collection sniper_devs_blocked, written by the bot):
# an auto-blocked creator counts when he sold at least this much NEAR or was auto-blocked by this many users.
SHARED_BLOCK_MIN_NEAR = 1.0
SHARED_BLOCK_MIN_USERS = 2

LAUNCH_SUPPLY = 10 ** 9  # tokens per Nearly / JustHoot launch
SUPPLY_PADS = ("nearly", "justhoot")
MAX_LIMIT = 200
MAX_CANDLES = 1000
MOVERS_MIN_USD = 500  # thin pool: one swap draws "×10" — not a mover

WS_PER_IP = 3
WS_MAX_CLIENTS = 2000  # per process: the dashboard shares it with the market and the intents relayer
WS_MAX_SUBS = 20
WS_QUEUE = 500
CHANNELS = ("launch", "trade", "move", "new", "network")
PERIODS = {"10m": 600, "1h": 3600, "24h": 86400, "7d": 7 * 86400}

# Labels: `label` is a proper name only (no language) or None; what the account is, says `kind`, and the site
# translates the kind itself. Suffix → (proper name of the bridge / launchpad, kind).
CONTRACT_SUFFIXES = {
    ".factory.bridge.near": ("Rainbow Bridge", "bridge"),
    ".omft.near": ("Omni Bridge", "bridge"),
    ".nearlytrade.near": ("Nearly", "token"),
    ".justhoot.near": ("JustHoot", "token"),
    # Intear launchpad tokens only: *.user.intear.near / *.wallet.intear.near are user wallets
    ".launch.intear.near": ("Intear", "token"),
    ".launch-v2.intear.near": ("Intear", "token"),
}
LEGACY_TEXT_LABELS = ("Крупный узел", "Агрегатор DEX")  # Russian descriptions stored before 2026-11-09 → None

# ─── Phase 3: link previews, holders, sign-in, layouts (2026-11-11) ──────────────

SITE_URL: str = _env("DASH_SITE_URL", "https://dash.sendler.xyz").rstrip("/")
API_URL: str = _env("DASH_API_URL", "https://api.sendler.xyz").rstrip("/")
# The site's built index.html on this server: /token/<id> is served from it with the token's og: tags (nginx of
# the site proxies that path here). Missing file → 503, nginx falls
# back to the static index.html.
SITE_INDEX: str = _env("DASH_SITE_INDEX", "../web/dist/index.html")
BOT_USERNAME: str = _env("DASH_BOT_USERNAME", "sendler_trade_bot")

# Sign-in. NEP-413 recipient and message the wallet signs (the site sends exactly these); the nonce is ours.
AUTH_RECIPIENT = "dash.sendler.xyz"
AUTH_MESSAGE = "Sign in to Sendler Dash"
AUTH_NONCE_TTL_S = 600
LOGIN_TTL_S = 600  # dash_logins: Telegram sign-in link (confirmed by the Sendler trade bot)
SESSION_TTL_DAYS = 30
AUTH_LIMIT_PER_IP = "20/minute"

# Collections written by the API (everything else under dash_* is the indexer's).
SESSIONS = "dash_sessions"
AUTH_NONCES = "dash_auth_nonces"
LOGINS = "dash_logins"  # must match the bot's collection name
LAYOUTS = "dash_layouts"

LAYOUT_MAX_BYTES = 64 * 1024
WATCHLIST_MAX = 100
SHARE_LIMIT_PER_IP = "30/hour"

HOLDERS_MAX = 100
