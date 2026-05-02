"""CloudSense FastAPI Application — Phases 1-4."""
from __future__ import annotations
import time
from contextlib import asynccontextmanager
import structlog
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from cloudsense.services.api.config import Settings, get_settings
from cloudsense.services.api.routers import agents, anomalies, budgets, connectors, costs, forecasting, ingestion
from cloudsense.services.api.routers.actions import router as actions_router
from cloudsense.services.api.routers.tags import router as tags_router
from cloudsense.services.db.clickhouse import ClickHouseClient

logger = structlog.get_logger()

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info("api_startup", env=settings.app_env, version="0.4.0")
    try:
        ch = ClickHouseClient(
            host=settings.clickhouse_host, port=settings.clickhouse_port,
            database=settings.clickhouse_db, user=settings.clickhouse_user,
            password=settings.clickhouse_password.get_secret_value())
        await ch.connect()
        await ch.init_schema()
        await ch.close()
        logger.info("clickhouse_schema_ready")
    except Exception as exc:
        logger.warning("clickhouse_schema_init_failed", error=str(exc))
    yield
    logger.info("api_shutdown")

def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="CloudSense API",
        description="FinOps Multi-Agent Platform — FOCUS 1.0 compliant. Phases 1-4.",
        version="0.4.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.debug else ["https://cloudsense.io"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def add_process_time_header(request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration = time.time() - start
        response.headers["X-Process-Time"] = str(duration)
        logger.debug("request_processed", method=request.method, path=request.url.path,
                     status=response.status_code, duration_ms=round(duration * 1000, 2))
        return response

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error("unhandled_exception", path=request.url.path, error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error"},
        )

    @app.get("/health", tags=["Health"])
    async def health() -> dict:
        return {"status": "ok", "service": "cloudsense-api", "version": "0.4.0",
                "phases": ["1-Foundation", "2-Agents", "3-Forecasting", "4-Actions"]}

    @app.get("/ready", tags=["Health"])
    async def ready() -> dict:
        return {"status": "ready"}

    # ── Phases 1-3 routers ─────────────────────────────────────
    app.include_router(costs.router, prefix="/api/v1")
    app.include_router(ingestion.router, prefix="/api/v1")
    app.include_router(connectors.router, prefix="/api/v1")
    app.include_router(agents.router, prefix="/api/v1")
    app.include_router(forecasting.router, prefix="/api/v1")
    app.include_router(anomalies.router, prefix="/api/v1")
    app.include_router(budgets.router, prefix="/api/v1")

    # ── Phase 4 routers ────────────────────────────────────────
    app.include_router(actions_router, prefix="/api/v1")
    app.include_router(tags_router, prefix="/api/v1")

    return app

app = create_app()
