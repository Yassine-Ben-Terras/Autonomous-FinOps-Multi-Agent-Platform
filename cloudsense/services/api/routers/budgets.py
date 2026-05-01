"""Budgets API (/api/v1/budgets/*)."""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from cloudsense.services.alerting.budget_alerts import BudgetAlertEngine
from cloudsense.services.api.config import Settings, get_settings
from cloudsense.services.api.deps import require_auth
from cloudsense.services.db.clickhouse import ClickHouseClient

router = APIRouter(prefix="/budgets", tags=["Budgets"])
_budgets: dict[str, float] = {}

class BudgetConfig(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    monthly_limit_usd: float = Field(..., gt=0)

class ThresholdConfig(BaseModel):
    warning: float = Field(default=0.75, ge=0.5, le=0.9)
    critical: float = Field(default=0.90, ge=0.7, le=0.95)
    breach: float = Field(default=1.00, ge=0.9, le=1.0)

@router.post("/configure", response_model=dict[str, Any])
async def configure_budget(payload: BudgetConfig, auth: str = Depends(require_auth)) -> dict[str, Any]:
    _budgets[payload.name] = payload.monthly_limit_usd
    return {"message": "Budget configured", "budget": payload.model_dump()}

@router.get("", response_model=dict[str, float])
async def list_budgets(auth: str = Depends(require_auth)) -> dict[str, float]:
    return _budgets

@router.post("/evaluate", response_model=list[dict[str, Any]])
async def evaluate_budgets(thresholds: ThresholdConfig | None = None, auth: str = Depends(require_auth),
                           settings: Settings = Depends(get_settings)) -> list[dict[str, Any]]:
    if not _budgets: raise HTTPException(status_code=400, detail="No budgets configured")
    ch = ClickHouseClient(host=settings.clickhouse_host, port=settings.clickhouse_port,
                          database=settings.clickhouse_db, user=settings.clickhouse_user,
                          password=settings.clickhouse_password.get_secret_value())
    await ch.connect()
    engine = BudgetAlertEngine(ch)
    if thresholds: engine.set_thresholds(thresholds.warning, thresholds.critical, thresholds.breach)
    alerts = await engine.evaluate_budgets(_budgets)
    await ch.close()
    return alerts

@router.get("/status/{budget_name}", response_model=dict[str, Any])
async def budget_status(budget_name: str, auth: str = Depends(require_auth),
                        settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    if budget_name not in _budgets: raise HTTPException(status_code=404, detail="Budget not found")
    ch = ClickHouseClient(host=settings.clickhouse_host, port=settings.clickhouse_port,
                          database=settings.clickhouse_db, user=settings.clickhouse_user,
                          password=settings.clickhouse_password.get_secret_value())
    await ch.connect()
    engine = BudgetAlertEngine(ch)
    alerts = await engine.evaluate_budgets({budget_name: _budgets[budget_name]})
    await ch.close()
    if alerts: return alerts[0]
    return {"budget_name": budget_name, "monthly_limit": _budgets[budget_name], "current_spend": 0,
            "utilization_percent": 0, "alert_level": "ok"}
