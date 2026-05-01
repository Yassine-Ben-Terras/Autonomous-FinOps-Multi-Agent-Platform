"""Cost Overview API (/api/v1/costs/*)."""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, Query
from cloudsense.services.api.config import Settings, get_settings
from cloudsense.services.api.deps import require_auth
from cloudsense.services.db.clickhouse import ClickHouseClient

router = APIRouter(prefix="/costs", tags=["Costs"])

async def _get_ch_client(settings: Settings = Depends(get_settings)) -> ClickHouseClient:
    client = ClickHouseClient(
        host=settings.clickhouse_host, port=settings.clickhouse_port,
        database=settings.clickhouse_db, user=settings.clickhouse_user,
        password=settings.clickhouse_password.get_secret_value())
    await client.connect()
    return client

@router.get("/overview", response_model=dict[str, Any])
async def cost_overview(
    provider: str | None = Query(None),
    days: int = Query(30, ge=1, le=365),
    auth: str = Depends(require_auth),
    settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    from datetime import date, timedelta
    end = date.today(); start = end - timedelta(days=days)
    client = await _get_ch_client(settings)
    try:
        rows = await client.query_cost_overview(provider=provider, start_date=start.isoformat(), end_date=end.isoformat())
        total_cost = sum(r["total_cost"] for r in rows)
        total_savings = sum(r["total_savings"] for r in rows)
        return {
            "period": {"start": start.isoformat(), "end": end.isoformat(), "days": days},
            "summary": {"total_effective_cost": round(total_cost, 2), "total_savings": round(total_savings, 2),
                        "total_usage_records": sum(r["record_count"] for r in rows)},
            "by_service": rows}
    finally: await client.close()

@router.get("/trend", response_model=list[dict[str, Any]])
async def cost_trend(provider: str | None = Query(None), days: int = Query(30, ge=7, le=365),
                     auth: str = Depends(require_auth), settings: Settings = Depends(get_settings)) -> list[dict[str, Any]]:
    client = await _get_ch_client(settings)
    try: return await client.query_daily_trend(provider=provider, days=days)
    finally: await client.close()

@router.get("/top-services", response_model=list[dict[str, Any]])
async def top_services(provider: str | None = Query(None), limit: int = Query(10, ge=1, le=50),
                       days: int = Query(30, ge=1, le=90), auth: str = Depends(require_auth),
                       settings: Settings = Depends(get_settings)) -> list[dict[str, Any]]:
    client = await _get_ch_client(settings)
    try: return await client.query_top_services(provider=provider, limit=limit, days=days)
    finally: await client.close()

@router.get("/by-team", response_model=dict[str, Any])
async def cost_by_team(team_tag: str = Query("team"), days: int = Query(30, ge=1, le=90),
                       auth: str = Depends(require_auth), settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    from datetime import date, timedelta
    end = date.today(); start = end - timedelta(days=days)
    client = await _get_ch_client(settings)
    try:
        query = """SELECT tags.%(tag)s AS team, sum(effective_cost) AS total_cost, count() AS resource_count
                   FROM focus_billing WHERE billing_period_start >= %(start)s AND billing_period_end <= %(end)s AND tags.%(tag)s != ''
                   GROUP BY team ORDER BY total_cost DESC"""
        result = await client._client.execute(query, {"tag": team_tag, "start": start.isoformat(), "end": end.isoformat()}, with_column_types=True)
        columns = [c[0] for c in result[1]]
        rows = [dict(zip(columns, row)) for row in result[0]]
        return {"team_tag": team_tag, "period_days": days, "allocations": rows}
    finally: await client.close()
