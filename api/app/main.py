"""Sendler Dash API — FastAPI entry point. Read-only REST + WebSocket over the collections the indexer writes."""
import logging
import sys

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from api.middleware import ApiKeyMiddleware
from config.env import CORS_ORIGINS, LOG_LEVEL
from core.database import Database
from core.limiter import limiter

# DB init before routes
Database.init()

from api.routes import dash, dash_me  # noqa: E402
from services import dash_auth, dash_ws  # noqa: E402

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logging.getLogger("uvicorn.access").disabled = True
logging.getLogger("httpx").setLevel(logging.WARNING)

app = FastAPI(
    title="Sendler Dash API",
    description="Public dashboard of NEAR memes and network: launches, tokens, candles, holders, smart money, "
                "big moves, network metrics, live WebSocket feed (/v2/dash).",
    version="1.0.0",
    openapi_tags=[{"name": "Sendler Dash", "description": "Everything under /v2/dash"}],
)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limited(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": f"Rate limit exceeded: {exc.detail}"})


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Log unhandled exceptions and return a generic 500 without a stack trace."""
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    logging.exception("Unhandled exception: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# Order: CORS (innermost) → rate limit → API key (outermost)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=600,
)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(ApiKeyMiddleware)

app.include_router(dash.router)
app.include_router(dash_me.router)


@app.get("/")
async def root():
    return {"message": "Sendler Dash API", "docs": "/docs"}


@app.get("/health")
async def health():
    """Health check for load balancers and monitoring."""
    return {"status": "ok"}


@app.on_event("startup")
async def startup():
    try:
        await dash_auth.ensure_indexes()
    except Exception:
        logging.exception("dash auth: ensure_indexes failed")


@app.on_event("shutdown")
async def shutdown():
    await dash_ws.HUB.stop()
    await Database.close()
