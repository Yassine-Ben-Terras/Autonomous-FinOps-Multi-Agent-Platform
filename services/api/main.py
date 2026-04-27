"""
CloudSense API — FastAPI Application
======================================
Main entry point for the CloudSense REST API.

Endpoints (Phase 1):
  GET  /health                       liveness probe
  GET  /api/v1/costs/overview        multi-cloud spend summary
  GET  /api/v1/costs/by-service      daily cost breakdown by service
  GET  /api/v1/costs/by-account      cost breakdown by account/subscription/project
  GET  /api/v1/costs/by-team         tag-based team allocation (showback)
  POST /api/v1/ingestion/trigger     manually trigger billing ingestion
  GET  /api/v1/connectors            list configured cloud connectors
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from services.api.config import settings
from services.api.db.clickhouse import get_clickhouse_client
from services.api.routers import costs, ingestion, connectors, health

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown lifecycle."""
    logger.info("CloudSense API starting up (env=%s)", settings.env)
    # Verify ClickHouse connection on startup
    try:
        ch = get_clickhouse_client()
        ch.command("SELECT 1")
        logger.info("ClickHouse connection: OK")
    except Exception as exc:
        logger.error("ClickHouse connection failed: %s", exc)

    yield

    logger.info("CloudSense API shutting down")


app = FastAPI(
    title="CloudSense API",
    description=(
        "FinOps multi-agent platform for AWS, Azure & GCP cost optimisation. "
        "All billing data is normalised to the FinOps FOCUS 1.0 specification."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── Middleware ──────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_request_timing(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Response-Time-Ms"] = f"{duration_ms:.1f}"
    return response


# ── Global error handler ────────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception at %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
            "message": "An unexpected error occurred. Check the logs for details.",
            "path": str(request.url.path),
        },
    )


# ── Routers ─────────────────────────────────────────────────────────────────────

app.include_router(health.router, tags=["health"])
app.include_router(costs.router,      prefix="/api/v1/costs",      tags=["costs"])
app.include_router(ingestion.router,  prefix="/api/v1/ingestion",  tags=["ingestion"])
app.include_router(connectors.router, prefix="/api/v1/connectors", tags=["connectors"])
