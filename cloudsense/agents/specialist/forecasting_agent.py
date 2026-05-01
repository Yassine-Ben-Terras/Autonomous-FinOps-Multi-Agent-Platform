"""
Forecasting Agent (Phase 3)

Generates 30/60/90-day cost projections using Prophet and XGBoost.
Triggers budget-breach early warnings. Integrates with MLflow.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4
import mlflow
import numpy as np
import pandas as pd
import structlog
from prophet import Prophet
from sklearn.metrics import mean_absolute_percentage_error
from cloudsense.agents.shared_types import CostInsight, InsightSeverity
from cloudsense.services.api.config import Settings, get_settings
from cloudsense.services.db.clickhouse import ClickHouseClient

logger = structlog.get_logger()

class ForecastingAgent:
    def __init__(self, clickhouse_client: ClickHouseClient, settings: Settings | None = None) -> None:
        self._ch = clickhouse_client
        self._settings = settings or get_settings()
        self._mlflow_setup()

    def _mlflow_setup(self) -> None:
        try:
            mlflow.set_tracking_uri(self._settings.mlflow_tracking_uri)
            mlflow.set_experiment(self._settings.mlflow_experiment_name)
        except Exception as exc:
            logger.warning("mlflow_setup_failed", error=str(exc))

    async def forecast(self, horizon_days: int = 90, granularity: str = "service") -> list[dict[str, Any]]:
        logger.info("forecasting_start", horizon=horizon_days, granularity=granularity)
        series = await self._fetch_time_series(granularity)
        forecasts: list[dict[str, Any]] = []
        with mlflow.start_run(run_name=f"forecast_{granularity}_{datetime.utcnow().isoformat()}"):
            mlflow.log_param("horizon_days", horizon_days)
            mlflow.log_param("granularity", granularity)
            for key, df in series.items():
                if len(df) < 14: continue
                model = Prophet(daily_seasonality=False, weekly_seasonality=True,
                                yearly_seasonality=len(df) > 365, interval_width=0.95)
                model.fit(df)
                future = model.make_future_dataframe(periods=horizon_days)
                forecast = model.predict(future)
                try:
                    mlflow.prophet.log_model(model, artifact_path=f"model_{key.replace('/', '_')}")
                except Exception: pass
                future_forecast = forecast.tail(horizon_days)
                total_projected = future_forecast["yhat"].sum()
                forecasts.append({
                    "forecast_id": str(uuid4()), "granularity": granularity, "key": key,
                    "horizon_days": horizon_days,
                    "projected_total_cost": round(float(total_projected), 2),
                    "confidence_interval": {
                        "lower": round(float(future_forecast["yhat_lower"].sum()), 2),
                        "upper": round(float(future_forecast["yhat_upper"].sum()), 2)},
                    "daily_forecast": [{"date": row["ds"].strftime("%Y-%m-%d"), "predicted": round(row["yhat"], 2),
                                        "lower": round(row["yhat_lower"], 2), "upper": round(row["yhat_upper"], 2)}
                                       for _, row in future_forecast.iterrows()],
                    "model": "prophet", "generated_at": datetime.utcnow().isoformat()})
            mlflow.log_metric("forecast_series_count", len(forecasts))
        logger.info("forecasting_complete", forecasts=len(forecasts))
        return forecasts

    async def check_budgets(self, budgets: dict[str, float] | None = None, horizon_days: int = 30) -> list[CostInsight]:
        insights: list[CostInsight] = []
        forecasts = await self.forecast(horizon_days=horizon_days, granularity="service")
        for fc in forecasts:
            key = fc["key"]
            budget = (budgets or {}).get(key, 0)
            if budget <= 0: continue
            projected_monthly = fc["projected_total_cost"]
            if projected_monthly > budget:
                overrun_pct = (projected_monthly - budget) / budget * 100
                severity = InsightSeverity.CRITICAL if overrun_pct > 50 else InsightSeverity.HIGH
                insights.append(CostInsight(
                    insight_id=str(uuid4()), agent="forecasting_agent", provider="multi",
                    severity=severity,
                    title=f"Budget breach forecast: {key}",
                    description=f"Projected ${projected_monthly:,.2f} vs budget ${budget:,.2f} ({overrun_pct:.1f}% overrun) over next {horizon_days} days.",
                    service_name=key, current_monthly_cost=Decimal(str(projected_monthly)),
                    projected_monthly_savings=Decimal("0"), confidence_score=0.85,
                    recommendation="Review auto-scaling policies, commit to reserved capacity, or adjust budget.",
                    action_type="investigate", risk_level="medium"))
        return insights

    async def _fetch_time_series(self, granularity: str) -> dict[str, pd.DataFrame]:
        import asyncio
        loop = asyncio.get_event_loop()
        group_by = {"service": "service_name", "team": "tags['team']", "environment": "tags['env']", "total": "provider"}.get(granularity, "service_name")
        query = f"""SELECT toDate(charge_period_start) AS ds, {group_by} AS key, sum(effective_cost) AS y
                    FROM focus_billing WHERE charge_period_start >= today() - INTERVAL 180 DAY
                    GROUP BY ds, key ORDER BY ds ASC"""
        result = await loop.run_in_executor(None, lambda: self._ch._client.execute(query, {}, with_column_types=True))
        columns = [c[0] for c in result[1]]
        rows = [dict(zip(columns, row)) for row in result[0]]
        if not rows: return {}
        df = pd.DataFrame(rows)
        df["ds"] = pd.to_datetime(df["ds"])
        df["y"] = df["y"].astype(float)
        series: dict[str, pd.DataFrame] = {}
        for key in df["key"].unique():
            series[key] = df[df["key"] == key][["ds", "y"]].copy()
        return series
