"""
Exports & Integrations API (Phase 5.2) — /api/v1/exports/*

FOCUS export endpoints:
  GET  /exports/focus              — Download FOCUS export (csv/parquet/jsonl/xlsx)
  GET  /exports/looker             — Looker parquet + LookML manifest
  GET  /exports/tableau            — Tableau XLSX + .tds data source
  GET  /exports/powerbi            — Power BI XLSX + M-script

Grafana datasource protocol:
  GET  /exports/grafana/health     — Health check
  POST /exports/grafana/search     — Metric search (autocomplete)
  POST /exports/grafana/query      — Time-series / table query
  GET  /exports/grafana/annotations— Anomaly annotations

Datadog:
  POST /exports/datadog/push-costs       — Push daily costs to Datadog
  POST /exports/datadog/push-event       — Push anomaly event to Datadog
  POST /exports/datadog/create-monitor   — Create budget monitor in Datadog
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from cloudsense.auth.deps import require_permission
from cloudsense.auth.models import Permission, TokenClaims
from cloudsense.exporters.focus_export import ExportFormat, FocusExportEngine
from cloudsense.exporters.bi_adapters import LookerAdapter, TableauAdapter, PowerBIAdapter
from cloudsense.integrations.grafana.plugin_backend import GrafanaPluginBackend
from cloudsense.integrations.datadog.integration import DatadogIntegration
from cloudsense.services.api.config import Settings, get_settings
from cloudsense.services.db.clickhouse import ClickHouseClient

router = APIRouter(prefix="/exports", tags=["Exports & Integrations (Phase 5.2)"])


# ── Shared dependency ─────────────────────────────────────────────────────────

async def _get_ch(settings: Settings = Depends(get_settings)) -> ClickHouseClient:
    ch = ClickHouseClient(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        database=settings.clickhouse_db,
        user=settings.clickhouse_user,
        password=settings.clickhouse_password.get_secret_value(),
    )
    await ch.connect()
    return ch


# ── Request models ─────────────────────────────────────────────────────────────

class GrafanaQueryRequest(BaseModel):
    range: dict[str, str] = Field(default_factory=dict)
    targets: list[dict[str, Any]] = Field(default_factory=list)
    maxDataPoints: int = 300
    intervalMs: int = 86400000


class GrafanaSearchRequest(BaseModel):
    query: str = ""


class DatadogPushCostsRequest(BaseModel):
    date: str = Field(..., description="YYYY-MM-DD")
    providers: list[str] | None = None


class DatadogAnomalyEventRequest(BaseModel):
    title: str
    text: str
    severity: str = "warning"
    provider: str = "unknown"
    service: str = "unknown"
    cost_delta: float = 0.0


class DatadogMonitorRequest(BaseModel):
    name: str
    service: str | None = None
    provider: str | None = None
    monthly_threshold: float = 1000.0
    notify_channels: list[str] = Field(default_factory=list)


# ── FOCUS Export endpoints ────────────────────────────────────────────────────

@router.get("/focus")
async def export_focus(
    format: str = Query("csv", description="csv | parquet | jsonl | xlsx"),
    start_date: str = Query(..., description="YYYY-MM-DD"),
    end_date: str = Query(..., description="YYYY-MM-DD"),
    providers: str | None = Query(None, description="Comma-separated: aws,azure,gcp"),
    billing_account_ids: str | None = Query(None, description="Comma-separated account IDs"),
    claims: TokenClaims = Depends(require_permission(Permission.REPORTS_EXPORT)),
    settings: Settings = Depends(get_settings),
) -> Response:
    """
    Download a FOCUS 1.0-compliant billing export.
    Supports CSV (default), Parquet, JSON Lines, and XLSX.
    """
    try:
        fmt = ExportFormat(format.lower())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid format: {format}. Use csv|parquet|jsonl|xlsx")

    ch = await _get_ch(settings)
    try:
        engine = FocusExportEngine(ch)
        result = await engine.export(
            format=fmt,
            start_date=start_date,
            end_date=end_date,
            providers=providers.split(",") if providers else None,
            billing_account_ids=billing_account_ids.split(",") if billing_account_ids else None,
        )
        return Response(
            content=result.content,
            media_type=result.mime_type,
            headers={
                "Content-Disposition": f'attachment; filename="{result.filename}"',
                "X-Row-Count": str(result.row_count),
                "X-Generated-At": result.generated_at,
            },
        )
    finally:
        await ch.close()


@router.get("/looker", response_model=dict[str, Any])
async def export_looker(
    start_date: str = Query(...),
    end_date: str = Query(...),
    providers: str | None = Query(None),
    billing_account_ids: str | None = Query(None),
    claims: TokenClaims = Depends(require_permission(Permission.REPORTS_EXPORT)),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Parquet export + LookML manifest for Looker."""
    ch = await _get_ch(settings)
    try:
        engine = FocusExportEngine(ch)
        adapter = LookerAdapter(engine)
        result = await adapter.export(
            start_date=start_date,
            end_date=end_date,
            providers=providers.split(",") if providers else None,
            billing_account_ids=billing_account_ids.split(",") if billing_account_ids else None,
        )
        # Return metadata + manifest (not the binary parquet in JSON)
        parquet_result = result["parquet"]
        return {
            "metadata": parquet_result.to_metadata(),
            "manifest": result["manifest"],
            "instructions": result["instructions"],
            "download_url": f"/api/v1/exports/focus?format=parquet&start_date={start_date}&end_date={end_date}",
        }
    finally:
        await ch.close()


@router.get("/tableau", response_model=dict[str, Any])
async def export_tableau(
    start_date: str = Query(...),
    end_date: str = Query(...),
    providers: str | None = Query(None),
    billing_account_ids: str | None = Query(None),
    claims: TokenClaims = Depends(require_permission(Permission.REPORTS_EXPORT)),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """XLSX export + Tableau Data Source (.tds) XML for Tableau Desktop."""
    ch = await _get_ch(settings)
    try:
        engine = FocusExportEngine(ch)
        adapter = TableauAdapter(engine)
        result = await adapter.export(
            start_date=start_date,
            end_date=end_date,
            providers=providers.split(",") if providers else None,
            billing_account_ids=billing_account_ids.split(",") if billing_account_ids else None,
        )
        xlsx_result = result["xlsx"]
        return {
            "metadata": xlsx_result.to_metadata(),
            "tds_xml": result["tds_xml"],
            "instructions": result["instructions"],
            "download_url": f"/api/v1/exports/focus?format=xlsx&start_date={start_date}&end_date={end_date}",
        }
    finally:
        await ch.close()


@router.get("/powerbi", response_model=dict[str, Any])
async def export_powerbi(
    start_date: str = Query(...),
    end_date: str = Query(...),
    providers: str | None = Query(None),
    billing_account_ids: str | None = Query(None),
    claims: TokenClaims = Depends(require_permission(Permission.REPORTS_EXPORT)),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """XLSX export + Power Query M-script + .pbids connection file for Power BI."""
    ch = await _get_ch(settings)
    try:
        engine = FocusExportEngine(ch)
        adapter = PowerBIAdapter(engine)
        result = await adapter.export(
            start_date=start_date,
            end_date=end_date,
            providers=providers.split(",") if providers else None,
            billing_account_ids=billing_account_ids.split(",") if billing_account_ids else None,
        )
        xlsx_result = result["xlsx"]
        return {
            "metadata": xlsx_result.to_metadata(),
            "power_query_m": result["power_query_m"],
            "pbids": result["pbids"],
            "instructions": result["instructions"],
            "download_url": f"/api/v1/exports/focus?format=xlsx&start_date={start_date}&end_date={end_date}",
        }
    finally:
        await ch.close()


# ── Grafana endpoints ─────────────────────────────────────────────────────────

@router.get("/grafana/health", response_model=dict[str, Any])
async def grafana_health(
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Grafana datasource health check (no auth — called by Grafana itself)."""
    ch = await _get_ch(settings)
    try:
        backend = GrafanaPluginBackend(ch)
        return await backend.health()
    finally:
        await ch.close()


@router.post("/grafana/search", response_model=list[str])
async def grafana_search(
    body: GrafanaSearchRequest,
    settings: Settings = Depends(get_settings),
) -> list[str]:
    """Grafana metric search — returns matching metric names for autocomplete."""
    ch = await _get_ch(settings)
    try:
        backend = GrafanaPluginBackend(ch)
        return await backend.search(body.query)
    finally:
        await ch.close()


@router.post("/grafana/query", response_model=dict[str, Any])
async def grafana_query(
    body: GrafanaQueryRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Grafana datasource query — returns time-series or table frames."""
    ch = await _get_ch(settings)
    try:
        backend = GrafanaPluginBackend(ch)
        return await backend.query(body.model_dump())
    finally:
        await ch.close()


@router.get("/grafana/annotations", response_model=list[dict[str, Any]])
async def grafana_annotations(
    from_: str = Query(alias="from", default=""),
    to: str = Query(default=""),
    query: str = Query(default="anomaly"),
    settings: Settings = Depends(get_settings),
) -> list[dict[str, Any]]:
    """Grafana annotations — returns cost anomalies as panel overlay markers."""
    from cloudsense.integrations.grafana.plugin_backend import _parse_grafana_time
    ch = await _get_ch(settings)
    try:
        backend = GrafanaPluginBackend(ch)
        return await backend.annotations(
            start_date=_parse_grafana_time(from_),
            end_date=_parse_grafana_time(to),
            query=query,
        )
    finally:
        await ch.close()


# ── Datadog endpoints ─────────────────────────────────────────────────────────

@router.post("/datadog/push-costs", response_model=dict[str, Any])
async def datadog_push_costs(
    body: DatadogPushCostsRequest,
    claims: TokenClaims = Depends(require_permission(Permission.REPORTS_EXPORT)),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Push daily cost metrics to Datadog (gauge metrics under cloudsense.cost.*)."""
    ch = await _get_ch(settings)
    try:
        dd = DatadogIntegration(ch, settings)
        return await dd.push_daily_costs(date=body.date, providers=body.providers)
    finally:
        await ch.close()


@router.post("/datadog/push-event", response_model=dict[str, Any])
async def datadog_push_event(
    body: DatadogAnomalyEventRequest,
    claims: TokenClaims = Depends(require_permission(Permission.REPORTS_EXPORT)),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Publish a cost anomaly as a Datadog Event (appears in Event Stream)."""
    ch = await _get_ch(settings)
    try:
        dd = DatadogIntegration(ch, settings)
        return await dd.push_anomaly_event(
            title=body.title,
            text=body.text,
            severity=body.severity,
            provider=body.provider,
            service=body.service,
            cost_delta=body.cost_delta,
        )
    finally:
        await ch.close()


@router.post("/datadog/create-monitor", response_model=dict[str, Any])
async def datadog_create_monitor(
    body: DatadogMonitorRequest,
    claims: TokenClaims = Depends(require_permission(Permission.SETTINGS_WRITE)),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Create a Datadog monitor for budget alerting on CloudSense cost metrics."""
    ch = await _get_ch(settings)
    try:
        dd = DatadogIntegration(ch, settings)
        return await dd.create_budget_monitor(
            name=body.name,
            service=body.service,
            provider=body.provider,
            monthly_threshold=body.monthly_threshold,
            notify_channels=body.notify_channels,
        )
    finally:
        await ch.close()
