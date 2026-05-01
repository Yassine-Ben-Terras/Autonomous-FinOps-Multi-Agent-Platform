"""Anomalies API (/api/v1/anomalies/*)."""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, Query
from cloudsense.agents.specialist.anomaly_agent import AnomalyDetectionAgent
from cloudsense.services.api.config import Settings, get_settings
from cloudsense.services.api.deps import require_auth
from cloudsense.services.db.clickhouse import ClickHouseClient

router = APIRouter(prefix="/anomalies", tags=["Anomalies"])

@router.get("/detect", response_model=list[dict[str, Any]])
async def detect_anomalies(days: int = Query(default=30, ge=7, le=90),
                           sensitivity: float = Query(default=0.95, ge=0.8, le=0.99),
                           auth: str = Depends(require_auth), settings: Settings = Depends(get_settings)) -> list[dict[str, Any]]:
    ch = ClickHouseClient(host=settings.clickhouse_host, port=settings.clickhouse_port,
                          database=settings.clickhouse_db, user=settings.clickhouse_user,
                          password=settings.clickhouse_password.get_secret_value())
    await ch.connect()
    agent = AnomalyDetectionAgent(ch)
    insights = await agent.analyze(time_range_days=days, sensitivity=sensitivity)
    await ch.close()
    return [i.model_dump(mode="json") for i in insights]

@router.post("/backtest", response_model=dict[str, Any])
async def backtest_anomalies(days: int = Query(default=90, ge=30, le=180),
                             auth: str = Depends(require_auth), settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    ch = ClickHouseClient(host=settings.clickhouse_host, port=settings.clickhouse_port,
                          database=settings.clickhouse_db, user=settings.clickhouse_user,
                          password=settings.clickhouse_password.get_secret_value())
    await ch.connect()
    agent = AnomalyDetectionAgent(ch)
    metrics = await agent.backtest(days=days)
    await ch.close()
    return {"backtest_period_days": days, "metrics_by_provider": metrics}
