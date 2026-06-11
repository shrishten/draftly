"""
Draftly – Gmail AI Reply Agent
Backend API entry point.

Run locally:
    uvicorn app.main:app --reload --port 8000
"""
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.db.database import init_db
from app.models.schemas import HealthResponse
from app.routers import auth, emails, drafts, users

# ── Logging setup ─────────────────────────────────────────────────────────────
settings = get_settings()

logging.basicConfig(
    stream  = sys.stdout,
    level   = getattr(logging, settings.log_level.upper(), logging.INFO),
    format  = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt = "%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Draftly API – initialising database …")
    await init_db()
    logger.info("Database ready. Draftly is up.")
    yield
    logger.info("Draftly shutting down.")


# ── App factory ───────────────────────────────────────────────────────────────
app = FastAPI(
    title       = "Draftly – Gmail AI Reply Agent",
    description = (
        "An AI-powered backend that fetches Gmail messages, generates intelligent "
        "reply drafts using Claude, and sends approved emails on behalf of the user."
    ),
    version     = "1.0.0",
    lifespan    = lifespan,
    docs_url    = "/docs",
    redoc_url   = "/redoc",
)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["http://localhost:3000", "http://localhost:5173"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)


# ── Global exception handler ──────────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception on %s %s: %s", request.method, request.url, exc, exc_info=True)
    return JSONResponse(
        status_code = 500,
        content     = {"detail": "An unexpected error occurred. Please try again later."},
    )


# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(emails.router)
app.include_router(drafts.router)
app.include_router(users.router)


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health():
    """Returns API health status."""
    return HealthResponse(env=settings.app_env)


@app.get("/", tags=["System"])
async def root():
    return {
        "name":    "Draftly – Gmail AI Reply Agent",
        "version": "1.0.0",
        "docs":    "/docs",
    }
