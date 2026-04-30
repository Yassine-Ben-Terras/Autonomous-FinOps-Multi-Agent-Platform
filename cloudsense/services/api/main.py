"""CloudSense FastAPI Application — Phases 1-5.2."""
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
from cloudsense.services.api.routers.auth import router as auth_router
from cloudsense.services.api.routers.k8s import router as k8s_router
from cloudsense.services.api.routers.exports import router as exports_router
from cloudsense.services.db.clickhouse import ClickHouseClient

logger = structlog.get_logger()

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info("api_startup", env=settings.app_env, version="0.5.2")
    try:
        ch = ClickHouseClient(
            host=settings.clickhouse_host, port=settings.clickhouse_port,
            database=settings.clickhouse_db, user=settings.clickhouse_user,
            password=settings.clickhouse_password.get_secret_value())
        await ch.connect()
        await ch.init_schema()
        await ch.close()
    except Exception as exc:
        logger.warning("clickhouse_schema_init_failed", error=str(exc))
    yield
    logger.info("api_shutdown")

def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="CloudSense API",
        description=(
            "FinOps Multi-Agent Platform — FOCUS 1.0 compliant.\n"
            "Phases 1-5.2: Multi-tenant SSO, K8s, Grafana plugin, "
            "Datadog integration, Looker/Tableau/Power BI exporters."
        ),
        version="0.5.2",
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
        response.headers["X-Process-Time"] = str(time.time() - start)
        return response

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error("unhandled_exception", path=request.url.path, error=str(exc))
        return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            content={"detail": "Internal server error"})

    @app.get("/health", tags=["Health"])
    async def health() -> dict:
        return {
            "status": "ok", "service": "cloudsense-api", "version": "0.5.2",
            "phases": [
                "1-Foundation", "2-Agents", "3-Forecasting",
                "4-Actions", "5.1-MultiTenant-SSO-K8s",
                "5.2-Grafana-Datadog-BI-Exporters"
            ]
        }

    @app.get("/ready", tags=["Health"])
    async def ready() -> dict:
        return {"status": "ready"}

    # Phase 1-3
    app.include_router(costs.router, prefix="/api/v1")
    app.include_router(ingestion.router, prefix="/api/v1")
    app.include_router(connectors.router, prefix="/api/v1")
    app.include_router(agents.router, prefix="/api/v1")
    app.include_router(forecasting.router, prefix="/api/v1")
    app.include_router(anomalies.router, prefix="/api/v1")
    app.include_router(budgets.router, prefix="/api/v1")
    # Phase 4
    app.include_router(actions_router, prefix="/api/v1")
    app.include_router(tags_router, prefix="/api/v1")
    # Phase 5.1
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(k8s_router, prefix="/api/v1")
    # Phase 5.2
    app.include_router(exports_router, prefix="/api/v1")

    return app

app = create_app()
