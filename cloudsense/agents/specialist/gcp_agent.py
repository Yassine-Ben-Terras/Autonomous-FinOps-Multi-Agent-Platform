"""GCP Cost Specialist Agent."""
from __future__ import annotations
from decimal import Decimal
from uuid import uuid4
import structlog
from cloudsense.agents.shared_types import CostInsight, InsightSeverity
from cloudsense.agents.tools.cost_tools import ClickHouseClient

logger = structlog.get_logger()

class GCPCostAgent:
    def __init__(self, clickhouse_client: ClickHouseClient) -> None:
        self._ch = clickhouse_client
    async def analyze(self, time_range_days: int = 30) -> list[CostInsight]:
        logger.info("gcp_agent_analysis_start", days=time_range_days)
        insights: list[CostInsight] = []
        sql = """SELECT resource_id, resource_name, region_id, sum(effective_cost) AS monthly_cost
                 FROM focus_billing WHERE provider = 'gcp' AND service_name = 'Virtual Machine'
                   AND billing_period_start >= today() - INTERVAL %(days)s DAY AND commitment_discount_id = ''
                 GROUP BY resource_id, resource_name, region_id HAVING monthly_cost > 200
                 ORDER BY monthly_cost DESC LIMIT 20"""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: self._ch._client.execute(sql, {"days": time_range_days}, with_column_types=True))
            columns = [c[0] for c in result[1]]
            rows = [dict(zip(columns, row)) for row in result[0]]
            for row in rows:
                monthly = Decimal(str(row.get("monthly_cost", 0)))
                insights.append(CostInsight(
                    insight_id=str(uuid4()), agent="gcp_cost_agent", provider="gcp",
                    severity=InsightSeverity.MEDIUM,
                    title=f"CUD gap: {row.get('resource_name', 'unknown')}",
                    description=f"GCE {row.get('resource_name')} in {row.get('region_id')} costs ${monthly:,.2f}/month with no CUD. 1-year CUD saves ~28-37%.",
                    resource_ids=[row.get("resource_id", "")], service_name="Virtual Machine", region=row.get("region_id"),
                    current_monthly_cost=monthly, projected_monthly_savings=monthly * Decimal("0.30"), confidence_score=0.82,
                    recommendation="Purchase 1-year CUD for steady-state compute workloads.",
                    action_type="purchase-commitment", risk_level="low"))
        except Exception as exc:
            logger.error("gcp_analysis_failed", error=str(exc))
        logger.info("gcp_agent_analysis_complete", insights=len(insights))
        return insights
