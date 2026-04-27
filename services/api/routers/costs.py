"""
CloudSense API — Cost Query Endpoints
========================================
All endpoints query the FOCUS billing table in ClickHouse.
Responses follow a consistent envelope: { data, meta, pagination }.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from services.api.db.clickhouse import get_clickhouse_client

router = APIRouter()


# ── Response models ───────────────────────────────────────────────────────────

class CostMeta(BaseModel):
    total_rows: int
    query_ms: float
    currency: str = "USD"
    focus_version: str = "1.0"


class CostOverviewItem(BaseModel):
    provider_name: str
    billing_account_id: str
    billing_account_name: str
    total_effective_cost: float
    total_list_cost: float
    total_billed_cost: float
    savings_amount: float          # list_cost - effective_cost
    savings_pct: float


class CostByServiceItem(BaseModel):
    charge_date: date
    provider_name: str
    service_name: str
    service_category: str
    region_id: str
    effective_cost: float


class CostByTeamItem(BaseModel):
    team_tag: str
    env_tag: str
    provider_name: str
    charge_date: date
    effective_cost: float


# ── Helper ────────────────────────────────────────────────────────────────────

def _default_date_range() -> tuple[date, date]:
    end   = date.today()
    start = end - timedelta(days=30)
    return start, end


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/overview", summary="Multi-cloud spend summary by account")
async def get_cost_overview(
    start_date: date  = Query(default=None, description="Start date (YYYY-MM-DD)"),
    end_date:   date  = Query(default=None, description="End date (YYYY-MM-DD)"),
    provider:   str | None = Query(default=None, description="Filter: aws | azure | gcp"),
    ch = Depends(get_clickhouse_client),
) -> dict[str, Any]:
    if not start_date or not end_date:
        start_date, end_date = _default_date_range()

    where_clauses = [
        f"charge_period_start >= '{start_date}'",
        f"charge_period_start <  '{end_date + timedelta(days=1)}'",
    ]
    if provider:
        where_clauses.append(f"provider_name = '{provider.lower()}'")

    where = " AND ".join(where_clauses)

    sql = f"""
        SELECT
            provider_name,
            billing_account_id,
            billing_account_name,
            sumMerge(total_effective)   AS total_effective_cost,
            sum(list_cost)              AS total_list_cost,
            sum(billed_cost)            AS total_billed_cost
        FROM focus.billing
        WHERE {where}
        GROUP BY provider_name, billing_account_id, billing_account_name
        ORDER BY total_effective_cost DESC
    """

    try:
        import time
        t0  = time.perf_counter()
        rows = ch.query(sql)
        elapsed = (time.perf_counter() - t0) * 1000
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"ClickHouse error: {exc}") from exc

    items: list[CostOverviewItem] = []
    for row in rows.result_rows:
        eff  = float(row[3] or 0)
        lst  = float(row[4] or 0)
        bil  = float(row[5] or 0)
        items.append(CostOverviewItem(
            provider_name=row[0],
            billing_account_id=row[1],
            billing_account_name=row[2],
            total_effective_cost=eff,
            total_list_cost=lst,
            total_billed_cost=bil,
            savings_amount=max(0.0, lst - eff),
            savings_pct=round((lst - eff) / lst * 100, 2) if lst > 0 else 0.0,
        ))

    return {
        "data": [item.model_dump() for item in items],
        "meta": CostMeta(
            total_rows=len(items),
            query_ms=round(elapsed, 2),
        ).model_dump(),
        "period": {"start": str(start_date), "end": str(end_date)},
    }


@router.get("/by-service", summary="Daily cost breakdown by service")
async def get_cost_by_service(
    start_date:   date = Query(default=None),
    end_date:     date = Query(default=None),
    provider:     str | None = Query(default=None),
    service_name: str | None = Query(default=None, description="Filter by service name"),
    granularity:  Literal["daily", "weekly", "monthly"] = Query(default="daily"),
    limit:        int = Query(default=500, le=5000),
    ch = Depends(get_clickhouse_client),
) -> dict[str, Any]:
    if not start_date or not end_date:
        start_date, end_date = _default_date_range()

    date_trunc = {
        "daily":   "toDate(charge_period_start)",
        "weekly":  "toMonday(charge_period_start)",
        "monthly": "toStartOfMonth(charge_period_start)",
    }[granularity]

    where_clauses = [
        f"charge_period_start >= '{start_date}'",
        f"charge_period_start <  '{end_date + timedelta(days=1)}'",
    ]
    if provider:
        where_clauses.append(f"provider_name = '{provider.lower()}'")
    if service_name:
        where_clauses.append(f"service_name = '{service_name}'")

    where = " AND ".join(where_clauses)

    sql = f"""
        SELECT
            {date_trunc}              AS charge_date,
            provider_name,
            service_name,
            service_category,
            region_id,
            SUM(effective_cost)       AS effective_cost
        FROM focus.billing
        WHERE {where}
        GROUP BY charge_date, provider_name, service_name, service_category, region_id
        ORDER BY charge_date DESC, effective_cost DESC
        LIMIT {limit}
    """

    import time
    t0   = time.perf_counter()
    rows = ch.query(sql)
    elapsed = (time.perf_counter() - t0) * 1000

    items = [
        CostByServiceItem(
            charge_date=row[0],
            provider_name=row[1],
            service_name=row[2],
            service_category=row[3],
            region_id=row[4],
            effective_cost=float(row[5] or 0),
        ).model_dump()
        for row in rows.result_rows
    ]

    return {
        "data": items,
        "meta": CostMeta(total_rows=len(items), query_ms=round(elapsed, 2)).model_dump(),
        "period": {"start": str(start_date), "end": str(end_date), "granularity": granularity},
    }


@router.get("/by-team", summary="Cost allocation by team tag (showback)")
async def get_cost_by_team(
    start_date: date = Query(default=None),
    end_date:   date = Query(default=None),
    provider:   str | None = Query(default=None),
    ch = Depends(get_clickhouse_client),
) -> dict[str, Any]:
    if not start_date or not end_date:
        start_date, end_date = _default_date_range()

    where_clauses = [
        f"charge_period_start >= '{start_date}'",
        f"charge_period_start <  '{end_date + timedelta(days=1)}'",
        "tags['team'] != ''",
    ]
    if provider:
        where_clauses.append(f"provider_name = '{provider.lower()}'")

    where = " AND ".join(where_clauses)

    sql = f"""
        SELECT
            tags['team']                          AS team_tag,
            tags['env']                           AS env_tag,
            provider_name,
            toDate(charge_period_start)           AS charge_date,
            SUM(effective_cost)                   AS effective_cost
        FROM focus.billing
        WHERE {where}
        GROUP BY team_tag, env_tag, provider_name, charge_date
        ORDER BY effective_cost DESC
        LIMIT 1000
    """

    import time
    t0   = time.perf_counter()
    rows = ch.query(sql)
    elapsed = (time.perf_counter() - t0) * 1000

    items = [
        CostByTeamItem(
            team_tag=row[0],
            env_tag=row[1] or "untagged",
            provider_name=row[2],
            charge_date=row[3],
            effective_cost=float(row[4] or 0),
        ).model_dump()
        for row in rows.result_rows
    ]

    return {
        "data": items,
        "meta": CostMeta(total_rows=len(items), query_ms=round(elapsed, 2)).model_dump(),
        "period": {"start": str(start_date), "end": str(end_date)},
    }


@router.get("/top-services", summary="Top N services by cost")
async def get_top_services(
    start_date: date = Query(default=None),
    end_date:   date = Query(default=None),
    provider:   str | None = Query(default=None),
    top_n:      int = Query(default=10, le=50),
    ch = Depends(get_clickhouse_client),
) -> dict[str, Any]:
    if not start_date or not end_date:
        start_date, end_date = _default_date_range()

    where_clauses = [
        f"charge_period_start >= '{start_date}'",
        f"charge_period_start <  '{end_date + timedelta(days=1)}'",
    ]
    if provider:
        where_clauses.append(f"provider_name = '{provider.lower()}'")

    sql = f"""
        SELECT
            provider_name,
            service_name,
            service_category,
            SUM(effective_cost)   AS total_cost,
            SUM(list_cost)        AS list_cost,
            COUNT(DISTINCT resource_id) AS resource_count
        FROM focus.billing
        WHERE {' AND '.join(where_clauses)}
        GROUP BY provider_name, service_name, service_category
        ORDER BY total_cost DESC
        LIMIT {top_n}
    """

    import time
    t0   = time.perf_counter()
    rows = ch.query(sql)
    elapsed = (time.perf_counter() - t0) * 1000

    items = [
        {
            "provider_name":    row[0],
            "service_name":     row[1],
            "service_category": row[2],
            "total_cost":       float(row[3] or 0),
            "list_cost":        float(row[4] or 0),
            "resource_count":   row[5],
            "waste_pct": round(
                max(0, float(row[4] or 0) - float(row[3] or 0)) / float(row[4] or 1) * 100, 2
            ),
        }
        for row in rows.result_rows
    ]

    return {
        "data": items,
        "meta": CostMeta(total_rows=len(items), query_ms=round(elapsed, 2)).model_dump(),
        "period": {"start": str(start_date), "end": str(end_date)},
    }
