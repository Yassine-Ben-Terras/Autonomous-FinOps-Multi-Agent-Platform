"""
Agent Tools — Shared utilities for cost analysis agents.

Each tool is a self-contained function that agents can invoke via
the LangGraph tool-calling mechanism. All tools are async and return
structured data that can be fed back into the agent's reasoning loop.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from services.api.config import get_settings
from services.api.db.clickhouse import ClickHouseClient

logger = logging.getLogger(__name__)


class CostAnalysisTools:
    """Collection of cost analysis tools for specialist agents."""

    def __init__(self) -> None:
        self.ch = ClickHouseClient.get_instance()

    async def get_provider_spend(
        self,
        provider: str,
        days: int = 30,
    ) -> dict[str, Any]:
        """Get total spend for a specific provider over N days."""
        end = datetime.now().date()
        start = end - timedelta(days=days)

        query = f"""
        SELECT
            sum(effective_cost) as total_cost,
            sum(list_cost - effective_cost) as total_savings,
            count() as record_count,
            uniqExact(service_name) as service_count
        FROM focus_billing
        WHERE provider = '{provider}'
          AND usage_period_start >= '{start.isoformat()}'
          AND usage_period_end <= '{end.isoformat()}'
        """

        rows = await self.ch.execute(query)
        if rows:
            row = rows[0]
            return {
                "provider": provider,
                "period_days": days,
                "total_cost": round(float(row[0]), 4),
                "total_savings": round(float(row[1]), 4),
                "record_count": row[2],
                "service_count": row[3],
                "currency": "USD",
            }
        return {
            "provider": provider,
            "period_days": days,
            "total_cost": 0.0,
            "total_savings": 0.0,
            "record_count": 0,
            "service_count": 0,
            "currency": "USD",
        }

    async def get_service_breakdown(
        self,
        provider: str,
        days: int = 30,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Get cost breakdown by service for a provider."""
        end = datetime.now().date()
        start = end - timedelta(days=days)

        query = f"""
        SELECT
            service_name,
            sum(effective_cost) as service_cost,
            sum(usage_quantity) as total_usage,
            count() as record_count
        FROM focus_billing
        WHERE provider = '{provider}'
          AND usage_period_start >= '{start.isoformat()}'
          AND usage_period_end <= '{end.isoformat()}'
        GROUP BY service_name
        ORDER BY service_cost DESC
        LIMIT {limit}
        """

        rows = await self.ch.execute(query)
        return [
            {
                "service_name": r[0],
                "cost": round(float(r[1]), 4),
                "usage": round(float(r[2]), 4),
                "records": r[3],
            }
            for r in rows
        ]

    async def get_daily_trend(
        self,
        provider: str,
        days: int = 30,
    ) -> list[dict[str, Any]]:
        """Get daily cost trend for anomaly detection."""
        end = datetime.now().date()
        start = end - timedelta(days=days)

        query = f"""
        SELECT
            toDate(usage_period_start) as day,
            sum(effective_cost) as daily_cost
        FROM focus_billing
        WHERE provider = '{provider}'
          AND usage_period_start >= '{start.isoformat()}'
          AND usage_period_end <= '{end.isoformat()}'
        GROUP BY day
        ORDER BY day
        """

        rows = await self.ch.execute(query)
        return [
            {
                "date": r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]),
                "cost": round(float(r[1]), 4),
            }
            for r in rows
        ]

    async def get_idle_resources(
        self,
        provider: str,
        min_cost: float = 10.0,
        days: int = 14,
    ) -> list[dict[str, Any]]:
        """Identify resources with very low usage but ongoing costs."""
        end = datetime.now().date()
        start = end - timedelta(days=days)

        query = f"""
        SELECT
            resource_id,
            service_name,
            region_id,
            sum(effective_cost) as total_cost,
            sum(usage_quantity) as total_usage,
            count() as billing_days
        FROM focus_billing
        WHERE provider = '{provider}'
          AND usage_period_start >= '{start.isoformat()}'
          AND usage_period_end <= '{end.isoformat()}'
        GROUP BY resource_id, service_name, region_id
        HAVING total_cost >= {min_cost}
           AND total_usage < 1.0
        ORDER BY total_cost DESC
        LIMIT 50
        """

        rows = await self.ch.execute(query)
        return [
            {
                "resource_id": r[0],
                "service_name": r[1],
                "region": r[2],
                "total_cost": round(float(r[3]), 4),
                "total_usage": round(float(r[4]), 4),
                "billing_days": r[5],
            }
            for r in rows
        ]

    async def get_commitment_coverage(
        self,
        provider: str,
        days: int = 30,
    ) -> dict[str, Any]:
        """Analyze commitment discount coverage (RI/SP/CUD)."""
        end = datetime.now().date()
        start = end - timedelta(days=days)

        query = f"""
        SELECT
            sum(effective_cost) as total_effective,
            sum(list_cost) as total_list,
            sumIf(effective_cost, commitment_discount_id != '') as committed_cost,
            countIf(commitment_discount_id != '') as committed_records,
            count() as total_records
        FROM focus_billing
        WHERE provider = '{provider}'
          AND usage_period_start >= '{start.isoformat()}'
          AND usage_period_end <= '{end.isoformat()}'
        """

        rows = await self.ch.execute(query)
        if rows:
            row = rows[0]
            total_eff = float(row[0]) if row[0] else 0
            committed = float(row[2]) if row[2] else 0
            total_list = float(row[1]) if row[1] else 0
            coverage = (committed / total_eff * 100) if total_eff > 0 else 0
            savings_rate = ((total_list - total_eff) / total_list * 100) if total_list > 0 else 0

            return {
                "provider": provider,
                "period_days": days,
                "total_effective_cost": round(total_eff, 4),
                "total_list_cost": round(total_list, 4),
                "committed_cost": round(committed, 4),
                "commitment_coverage_pct": round(coverage, 2),
                "savings_rate_pct": round(savings_rate, 2),
                "opportunity": round(total_eff - committed, 4) if coverage < 70 else 0,
            }
        return {
            "provider": provider,
            "period_days": days,
            "total_effective_cost": 0.0,
            "commitment_coverage_pct": 0.0,
            "opportunity": 0.0,
        }

    async def get_tag_coverage(
        self,
        provider: str,
        days: int = 30,
    ) -> dict[str, Any]:
        """Analyze tagging compliance for resources."""
        end = datetime.now().date()
        start = end - timedelta(days=days)

        query = f"""
        SELECT
            count() as total_records,
            countIf(arrayExists(t -> t.1 = 'team', tags)) as tagged_records,
            countIf(arrayExists(t -> t.1 = 'environment', tags)) as env_tagged,
            countIf(arrayExists(t -> t.1 = 'owner', tags)) as owner_tagged,
            countIf(length(tags) = 0) as untagged_records
        FROM focus_billing
        WHERE provider = '{provider}'
          AND usage_period_start >= '{start.isoformat()}'
          AND usage_period_end <= '{end.isoformat()}'
        """

        rows = await self.ch.execute(query)
        if rows:
            row = rows[0]
            total = row[0]
            tagged = row[1]
            untagged = row[4]
            return {
                "provider": provider,
                "period_days": days,
                "total_records": total,
                "team_tag_coverage_pct": round(tagged / total * 100, 2) if total > 0 else 0,
                "env_tag_coverage_pct": round(row[2] / total * 100, 2) if total > 0 else 0,
                "owner_tag_coverage_pct": round(row[3] / total * 100, 2) if total > 0 else 0,
                "untagged_records": untagged,
                "untagged_cost": 0.0,  # Would need separate query
            }
        return {
            "provider": provider,
            "period_days": days,
            "total_records": 0,
            "team_tag_coverage_pct": 0.0,
        }

    async def get_cost_by_region(
        self,
        provider: str,
        days: int = 30,
    ) -> list[dict[str, Any]]:
        """Get cost breakdown by region."""
        end = datetime.now().date()
        start = end - timedelta(days=days)

        query = f"""
        SELECT
            region_id,
            sum(effective_cost) as region_cost,
            count() as record_count
        FROM focus_billing
        WHERE provider = '{provider}'
          AND usage_period_start >= '{start.isoformat()}'
          AND usage_period_end <= '{end.isoformat()}'
        GROUP BY region_id
        ORDER BY region_cost DESC
        """

        rows = await self.ch.execute(query)
        return [
            {
                "region": r[0],
                "cost": round(float(r[1]), 4),
                "records": r[2],
            }
            for r in rows
        ]

    def format_insight_context(self, data: dict[str, Any]) -> str:
        """Format tool output as natural language context for LLM reasoning."""
        lines = ["\n--- Cost Analysis Data ---"]
        for key, value in data.items():
            if isinstance(value, float):
                lines.append(f"{key}: {value:.4f}")
            elif isinstance(value, list) and len(value) > 5:
                lines.append(f"{key}: [{len(value)} items]")
                for item in value[:5]:
                    lines.append(f"  - {item}")
                if len(value) > 5:
                    lines.append(f"  ... and {len(value) - 5} more")
            else:
                lines.append(f"{key}: {value}")
        lines.append("---\n")
        return "\n".join(lines)
