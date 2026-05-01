"""Forecasting API (/api/v1/forecasting/*)."""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from cloudsense.agents.specialist.forecasting_agent import ForecastingAgent
from cloudsense.services.api.config import Settings, get_settings
from cloudsense.services.api.deps import require_auth
from cloudsense.services.db.clickhouse import ClickHouseClient

router = APIRouter(prefix="/forecasting", tags=["Forecasting"])

class ForecastRequest(BaseModel):
    horizon_days: int = Field(default=90, ge=30, le=365)
    granularity: str = Field(default="service", pattern="^(service|team|environment|total)$")

@router.post("/generate", response_model=list[dict[str, Any]])
async def generate_forecast(payload: ForecastRequest, auth: str = Depends(require_auth),
                            settings: Settings = Depends(get_settings)) -> list[dict[str, Any]]:
    ch = ClickHouseClient(host=settings.clickhouse_host, port=settings.clickhouse_port,
                          database=settings.clickhouse_db, user=settings.clickhouse_user,
                          password=settings.clickhouse_password.get_secret_value())
    await ch.connect()
    agent = ForecastingAgent(ch, settings)
    result = await agent.forecast(horizon_days=payload.horizon_days, granularity=payload.granularity)
    await ch.close()
    return result

@router.get("/summary", response_model=dict[str, Any])
async def forecast_summary(horizon_days: int = Query(default=90, ge=30, le=365),
                           auth: str = Depends(require_auth), settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    ch = ClickHouseClient(host=settings.clickhouse_host, port=settings.clickhouse_port,
                          database=settings.clickhouse_db, user=settings.clickhouse_user,
                          password=settings.clickhouse_password.get_secret_value())
    await ch.connect()
    agent = ForecastingAgent(ch, settings)
    forecasts = await agent.forecast(horizon_days=horizon_days, granularity="total")
    await ch.close()
    total_projected = sum(f["projected_total_cost"] for f in forecasts)
    return {"horizon_days": horizon_days, "total_projected_cost": round(total_projected, 2),
            "series_count": len(forecasts), "by_provider": forecasts}

@router.get("/models", response_model=list[dict[str, Any]])
async def list_models(auth: str = Depends(require_auth)) -> list[dict[str, Any]]:
    import mlflow
    settings = get_settings()
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    experiment = mlflow.get_experiment_by_name(settings.mlflow_experiment_name)
    if not experiment: return []
    runs = mlflow.search_runs(experiment_ids=[experiment.experiment_id])
    return runs[["run_id", "start_time", "params.horizon_days", "metrics.forecast_series_count"]].to_dict(orient="records")
