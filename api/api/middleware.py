"""API key check. The dashboard is called from the browser without a key; the check exists so the same process can
host key-protected routes and so the dashboard can be put back behind a key (DASH_REQUIRE_API_KEY=1)."""
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

API_KEY_HEADER = "X-API-Key"
PUBLIC_PATHS = frozenset(["/", "/health", "/docs", "/redoc", "/openapi.json"])
DASH_PUBLIC_PREFIX = "/v2/dash/"


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Validate X-API-Key. Bypass if ALLOWED_API_KEYS is empty."""

    async def dispatch(self, request: Request, call_next):
        from config.env import ALLOWED_API_KEYS

        if not ALLOWED_API_KEYS.strip():
            return await call_next(request)

        # OPTIONS preflight: no X-API-Key sent by browser — must pass for CORS
        if request.method == "OPTIONS":
            return await call_next(request)

        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        # Sendler Dash: public read-only dashboard called from the browser without a key;
        # protected by the rate limit and its own cache. DASH_REQUIRE_API_KEY=1 puts it back behind the key.
        from config.dash import DASH_REQUIRE_API_KEY
        if request.url.path.startswith(DASH_PUBLIC_PREFIX) and not DASH_REQUIRE_API_KEY:
            return await call_next(request)

        key = request.headers.get(API_KEY_HEADER)
        allowed = {k.strip() for k in ALLOWED_API_KEYS.split(",") if k.strip()}
        if key and key in allowed:
            return await call_next(request)
        return JSONResponse(status_code=403, content={"detail": "Invalid or missing API key"})
