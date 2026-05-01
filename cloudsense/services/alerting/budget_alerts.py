"""Budget Breach Early-Warning System."""
from __future__ import annotations
from datetime import datetime
from typing import Any
import structlog
from cloudsense.services.db.clickhouse import ClickHouseClient

logger = structlog.get_logger()

class BudgetAlertEngine:
    def __init__(self, clickhouse_client: ClickHouseClient) -> None:
        self._ch = clickhouse_client
        self._thresholds = {"warning": 0.75, "critical": 0.90, "breach": 1.00}

    def set_thresholds(self, warning: float = 0.75, critical: float = 0.90, breach: float = 1.00) -> None:
        self._thresholds = {"warning": warning, "critical": critical, "breach": breach}

    async def evaluate_budgets(self, budgets: dict[str, float], lookback_days: int = 30) -> list[dict[str, Any]]:
        alerts = []
        current_spend = await self._get_current_month_spend()
        for name, limit in budgets.items():
            spend = current_spend.get(name, 0.0)
            ratio = spend / limit if limit > 0 else 0
            if ratio >= self._thresholds["breach"]: level = "breach"
            elif ratio >= self._thresholds["critical"]: level = "critical"
            elif ratio >= self._thresholds["warning"]: level = "warning"
            else: continue
            alerts.append({"budget_name": name, "monthly_limit": limit, "current_spend": round(spend, 2),
                           "utilization_percent": round(ratio * 100, 1), "alert_level": level,
                           "projected_overrun": round(max(0, spend - limit), 2),
                           "timestamp": datetime.utcnow().isoformat()})
        logger.info("budget_evaluation_complete", alerts=len(alerts))
        return alerts

    async def _get_current_month_spend(self) -> dict[str, float]:
        import asyncio
        loop = asyncio.get_event_loop()
        query = """SELECT service_name, sum(effective_cost) AS spend FROM focus_billing
                   WHERE toYYYYMM(billing_period_start) = toYYYYMM(today()) GROUP BY service_name"""
        result = await loop.run_in_executor(None, lambda: self._ch._client.execute(query, {}, with_column_types=True))
        columns = [c[0] for c in result[1]]
        rows = [dict(zip(columns, row)) for row in result[0]]
        return {r["service_name"]: float(r["spend"]) for r in rows}
