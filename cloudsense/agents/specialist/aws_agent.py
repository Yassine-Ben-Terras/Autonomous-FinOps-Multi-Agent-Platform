"""AWS Cost Specialist Agent."""
from __future__ import annotations
from decimal import Decimal
from typing import Any
from uuid import uuid4
import structlog
from langchain import hub
from langchain.agents import AgentExecutor, create_react_agent
from langchain_anthropic import ChatAnthropic
from cloudsense.agents.shared_types import CostInsight, InsightSeverity, InsightStatus
from cloudsense.agents.tools.cost_tools import ClickHouseClient, ClickHouseQueryTool, ResourceListTool
from cloudsense.services.api.config import Settings, get_settings

logger = structlog.get_logger()

class AWSCostAgent:
    def __init__(self, clickhouse_client: ClickHouseClient, settings: Settings | None = None) -> None:
        self._ch = clickhouse_client
        self._settings = settings or get_settings()
        self._llm = ChatAnthropic(
            model=self._settings.llm_default_model,
            anthropic_api_key=self._settings.anthropic_api_key.get_secret_value() if self._settings.anthropic_api_key else None,
            temperature=0.1)
        self._tools = [ClickHouseQueryTool(clickhouse_client), ResourceListTool(clickhouse_client)]
        prompt = hub.pull("hwchase17/react")
        agent = create_react_agent(self._llm, self._tools, prompt)
        self._executor = AgentExecutor(agent=agent, tools=self._tools, verbose=False, handle_parsing_errors=True)

    async def analyze(self, time_range_days: int = 30) -> list[CostInsight]:
        logger.info("aws_agent_analysis_start", days=time_range_days)
        insights = await self._heuristic_analysis(time_range_days)
        logger.info("aws_agent_analysis_complete", insights=len(insights))
        return insights

    async def _heuristic_analysis(self, days: int) -> list[CostInsight]:
        insights: list[CostInsight] = []
        sql_idle = """SELECT resource_id, resource_name, region_id, sum(effective_cost) AS monthly_cost,
                             avg(usage_quantity) AS avg_daily_usage, stddevPop(usage_quantity) AS usage_variance
                      FROM focus_billing WHERE provider = 'aws' AND service_name = 'Virtual Machine'
                        AND billing_period_start >= today() - INTERVAL %(days)s DAY
                      GROUP BY resource_id, resource_name, region_id
                      HAVING monthly_cost > 50 AND avg_daily_usage < 2 AND usage_variance < 1
                      ORDER BY monthly_cost DESC LIMIT 20"""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: self._ch._client.execute(sql_idle, {"days": days}, with_column_types=True))
            columns = [c[0] for c in result[1]]
            rows = [dict(zip(columns, row)) for row in result[0]]
            for row in rows:
                insights.append(CostInsight(
                    insight_id=str(uuid4()), agent="aws_cost_agent", provider="aws",
                    severity=InsightSeverity.HIGH,
                    title=f"Potentially idle EC2: {row.get('resource_id', 'unknown')}",
                    description=f"EC2 {row.get('resource_name')} in {row.get('region_id')} has low usage ({row.get('avg_daily_usage', 0):.2f} hrs/day) but costs ${row.get('monthly_cost', 0):.2f}/month.",
                    resource_ids=[row.get("resource_id", "")], service_name="Virtual Machine", region=row.get("region_id"),
                    current_monthly_cost=Decimal(str(row.get("monthly_cost", 0))),
                    projected_monthly_savings=Decimal(str(row.get("monthly_cost", 0))) * Decimal("0.8"),
                    confidence_score=0.75,
                    recommendation="Review CloudWatch CPU. If <5% avg over 14 days, stop or downsize.",
                    action_type="stop", risk_level="low"))
        except Exception as exc:
            logger.error("aws_idle_query_failed", error=str(exc))

        sql_commitment = """SELECT service_name, sum(effective_cost) AS monthly_cost, sum(list_cost - effective_cost) AS existing_savings
                            FROM focus_billing WHERE provider = 'aws' AND billing_period_start >= today() - INTERVAL %(days)s DAY
                              AND commitment_discount_id = '' AND service_name IN ('Virtual Machine', 'Relational Database')
                            GROUP BY service_name HAVING monthly_cost > 500 ORDER BY monthly_cost DESC"""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: self._ch._client.execute(sql_commitment, {"days": days}, with_column_types=True))
            columns = [c[0] for c in result[1]]
            rows = [dict(zip(columns, row)) for row in result[0]]
            for row in rows:
                monthly = Decimal(str(row.get("monthly_cost", 0)))
                insights.append(CostInsight(
                    insight_id=str(uuid4()), agent="aws_cost_agent", provider="aws",
                    severity=InsightSeverity.MEDIUM,
                    title=f"Commitment gap: {row.get('service_name')}",
                    description=f"${monthly:,.2f}/month on {row.get('service_name')} with no RIs/SPs. Potential 30-40% savings.",
                    service_name=row.get("service_name"), current_monthly_cost=monthly,
                    projected_monthly_savings=monthly * Decimal("0.35"), confidence_score=0.85,
                    recommendation="Purchase convertible Reserved Instances for baseline capacity.",
                    action_type="purchase-commitment", risk_level="low"))
        except Exception as exc:
            logger.error("aws_commitment_query_failed", error=str(exc))
        return insights
