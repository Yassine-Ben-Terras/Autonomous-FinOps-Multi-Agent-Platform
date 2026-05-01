"""
Anomaly Detection Agent (Phase 3)

Monitors real-time billing streams and detects statistical anomalies
using Prophet and ARIMA. Attributes cost spikes to services/teams/regions.
"""
from __future__ import annotations
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4
import numpy as np
import pandas as pd
import structlog
from prophet import Prophet
from cloudsense.agents.shared_types import CostInsight, InsightSeverity, InsightStatus
from cloudsense.services.db.clickhouse import ClickHouseClient

logger = structlog.get_logger()

class AnomalyDetectionAgent:
    def __init__(self, clickhouse_client: ClickHouseClient) -> None:
        self._ch = clickhouse_client
        self._model_cache: dict[str, Prophet] = {}

    async def analyze(self, time_range_days: int = 30, sensitivity: float = 0.95) -> list[CostInsight]:
        logger.info("anomaly_agent_start", days=time_range_days, sensitivity=sensitivity)
        insights: list[CostInsight] = []
        series = await self._fetch_daily_series(time_range_days)
        for provider, df in series.items():
            if len(df) < 14:
                logger.warning("anomaly_insufficient_data", provider=provider, rows=len(df))
                continue
            anomalies = self._detect_with_prophet(df, sensitivity=sensitivity)
            for anomaly in anomalies:
                insights.append(CostInsight(
                    insight_id=str(uuid4()), agent="anomaly_agent", provider=provider,
                    severity=InsightSeverity.CRITICAL if anomaly["severity"] == "critical" else InsightSeverity.HIGH,
                    title=f"Cost anomaly detected: {provider.upper()}",
                    description=f"Unexpected cost spike on {anomaly['date']}: ${anomaly['actual_cost']:,.2f} (expected ${anomaly['expected_cost']:,.2f} ± ${anomaly['uncertainty']:,.2f}). Deviation: {anomaly['deviation_percent']:.1f}%",
                    service_name=anomaly.get("service_name"), region=anomaly.get("region_id"),
                    current_monthly_cost=Decimal(str(anomaly["actual_cost"])),
                    projected_monthly_savings=Decimal("0"), confidence_score=anomaly["confidence"],
                    recommendation=f"Investigate {anomaly.get('service_name', 'services')} in {anomaly.get('region_id', 'unknown region')}. Check for misconfigurations or unauthorized usage.",
                    action_type="investigate", risk_level="high"))
        logger.info("anomaly_agent_complete", insights=len(insights))
        return insights

    async def _fetch_daily_series(self, days: int) -> dict[str, pd.DataFrame]:
        import asyncio
        loop = asyncio.get_event_loop()
        query = """SELECT toDate(charge_period_start) AS ds, provider, service_name, region_id, sum(effective_cost) AS y
                   FROM focus_billing WHERE charge_period_start >= today() - INTERVAL %(days)s DAY
                   GROUP BY ds, provider, service_name, region_id ORDER BY ds ASC"""
        result = await loop.run_in_executor(None, lambda: self._ch._client.execute(query, {"days": days}, with_column_types=True))
        columns = [c[0] for c in result[1]]
        rows = [dict(zip(columns, row)) for row in result[0]]
        if not rows: return {}
        df = pd.DataFrame(rows)
        df["ds"] = pd.to_datetime(df["ds"])
        df["y"] = df["y"].astype(float)
        series: dict[str, pd.DataFrame] = {}
        for provider in df["provider"].unique():
            series[provider] = df[df["provider"] == provider].groupby("ds")["y"].sum().reset_index()
        return series

    def _detect_with_prophet(self, df: pd.DataFrame, sensitivity: float = 0.95) -> list[dict[str, Any]]:
        if len(df) < 14: return []
        model = Prophet(daily_seasonality=False, weekly_seasonality=True, yearly_seasonality=False,
                        interval_width=sensitivity, changepoint_prior_scale=0.05)
        model.fit(df)
        future = model.make_future_dataframe(periods=3)
        forecast = model.predict(future)
        merged = df.merge(forecast[["ds", "yhat", "yhat_lower", "yhat_upper"]], on="ds")
        anomalies = []
        for _, row in merged.iterrows():
            actual, expected, lower, upper = row["y"], row["yhat"], row["yhat_lower"], row["yhat_upper"]
            if actual > upper or actual < lower:
                deviation = abs(actual - expected) / expected * 100 if expected > 0 else 0
                anomalies.append({
                    "date": row["ds"].strftime("%Y-%m-%d"), "actual_cost": actual,
                    "expected_cost": expected, "uncertainty": (upper - lower) / 2,
                    "deviation_percent": deviation, "confidence": sensitivity,
                    "severity": "critical" if deviation > 50 else "high"})
        return anomalies

    async def backtest(self, days: int = 90, train_ratio: float = 0.8) -> dict[str, Any]:
        series = await self._fetch_daily_series(days)
        metrics: dict[str, Any] = {}
        for provider, df in series.items():
            if len(df) < 30: continue
            split_idx = int(len(df) * train_ratio)
            train_df, test_df = df.iloc[:split_idx], df.iloc[split_idx:]
            model = Prophet(daily_seasonality=False, weekly_seasonality=True, yearly_seasonality=False)
            model.fit(train_df)
            future = test_df[["ds"]].copy()
            forecast = model.predict(future)
            merged = test_df.merge(forecast[["ds", "yhat"]], on="ds")
            mape = np.mean(np.abs((merged["y"] - merged["yhat"]) / merged["y"])) * 100
            rmse = np.sqrt(np.mean((merged["y"] - merged["yhat"]) ** 2))
            metrics[provider] = {"mape_percent": round(float(mape), 2), "rmse": round(float(rmse), 2),
                                 "train_points": len(train_df), "test_points": len(test_df)}
        return metrics
