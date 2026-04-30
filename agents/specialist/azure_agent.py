"""
Azure Cost Agent — Specialist sub-agent for Azure cost analysis.

Analyzes Azure billing data to detect:
- Idle/unused VMs and App Service plans
- Right-sizing opportunities via Azure Advisor
- Hybrid Benefit gaps
- Unused managed disks
- Tag compliance issues
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from agents.shared_types import (
    AgentStatus,
    AgentTask,
    CostInsight,
    RecommendationCategory,
    RiskLevel,
)
from agents.tools.cost_tools import CostAnalysisTools
from connectors.azure.cost_connector import AzureCostConnector
from services.api.config import get_settings

logger = logging.getLogger(__name__)


class AzureCostAgent:
    """Azure specialist cost analysis agent."""

    provider: str = "azure"
    display_name: str = "Azure Cost Agent"

    def __init__(self) -> None:
        self.tools = CostAnalysisTools()

    async def execute(self, task: AgentTask) -> AgentTask:
        """Execute an analysis task and produce insights."""
        task.status = AgentStatus.RUNNING
        task.started_at = datetime.utcnow()
        logger.info("Azure agent executing task: %s", task.task_id)

        try:
            # Gather data
            spend_data = await self.tools.get_provider_spend("azure", days=30)
            services = await self.tools.get_service_breakdown("azure", days=30)
            idle = await self.tools.get_idle_resources("azure", days=14)
            commitment = await self.tools.get_commitment_coverage("azure", days=30)
            tags = await self.tools.get_tag_coverage("azure", days=30)
            regions = await self.tools.get_cost_by_region("azure", days=30)

            insights: list[CostInsight] = []

            # Overview
            insights.append(
                CostInsight(
                    provider="azure",
                    category=RecommendationCategory.GENERAL,
                    title=f"Azure Monthly Spend: ${spend_data['total_cost']:,.2f}",
                    description=(
                        f"Azure spend for last {spend_data['period_days']} days: "
                        f"${spend_data['total_cost']:,.2f} across {spend_data['service_count']} services."
                    ),
                    current_monthly_cost=spend_data["total_cost"],
                    projected_monthly_savings=spend_data["total_savings"],
                    confidence_score=0.95,
                    risk_level=RiskLevel.LOW,
                )
            )

            # Top service
            if services:
                top = services[0]
                insights.append(
                    CostInsight(
                        provider="azure",
                        category=RecommendationCategory.GENERAL,
                        title=f"Top Azure Service: {top['service_name']} (${top['cost']:,.2f})",
                        description=f"Highest spend: {top['service_name']} at ${top['cost']:,.2f}",
                        current_monthly_cost=top["cost"],
                        confidence_score=0.9,
                        risk_level=RiskLevel.LOW,
                    )
                )

            # Idle resources
            if idle:
                total_cost = sum(r["total_cost"] for r in idle)
                insights.append(
                    CostInsight(
                        provider="azure",
                        category=RecommendationCategory.IDLE_RESOURCE,
                        title=f"{len(idle)} potentially idle Azure resources",
                        description=f"Resources with cost but minimal usage, total: ${total_cost:,.2f}",
                        current_monthly_cost=total_cost,
                        projected_monthly_savings=total_cost * 0.8,
                        confidence_score=0.75,
                        risk_level=RiskLevel.MEDIUM,
                        action_required=True,
                        suggested_action="Review idle VMs and disks, consider deallocation",
                    )
                )

            # Commitment gap
            if commitment["commitment_coverage_pct"] < 70:
                insights.append(
                    CostInsight(
                        provider="azure",
                        category=RecommendationCategory.COMMITMENT_GAP,
                        title=f"Azure reservation coverage: {commitment['commitment_coverage_pct']:.1f}%",
                        description=(
                            f"Reservation coverage is {commitment['commitment_coverage_pct']:.1f}%. "
                            f"Opportunity to commit ${commitment['opportunity']:,.2f} for savings."
                        ),
                        current_monthly_cost=commitment["total_effective_cost"],
                        projected_monthly_savings=commitment["opportunity"] * 0.25,
                        confidence_score=0.8,
                        risk_level=RiskLevel.LOW,
                        action_required=True,
                        suggested_action="Purchase Azure Reservations for stable VM workloads",
                    )
                )

            # Tag compliance
            if tags["team_tag_coverage_pct"] < 80:
                insights.append(
                    CostInsight(
                        provider="azure",
                        category=RecommendationCategory.TAG_COMPLIANCE,
                        title=f"Azure tag compliance: {tags['team_tag_coverage_pct']:.1f}%",
                        description=f"{tags['untagged_records']} untagged resources prevent chargeback",
                        confidence_score=0.85,
                        risk_level=RiskLevel.LOW,
                        action_required=True,
                        suggested_action="Enforce Azure Policy for mandatory tags",
                    )
                )

            # Native recommendations
            native_recs = await self.get_native_recommendations()
            for rec in native_recs[:5]:
                insights.append(
                    CostInsight(
                        provider="azure",
                        category=RecommendationCategory.GENERAL,
                        title=f"Azure Advisor: {rec.get('title', 'Recommendation')}",
                        description=rec.get("description", ""),
                        projected_monthly_savings=float(rec.get("potential_savings", 0)) / 12,
                        confidence_score=0.7,
                        risk_level=RiskLevel.LOW,
                        action_required=True,
                        metadata={"source": "azure_advisor", "raw": rec},
                    )
                )

            task.result = insights
            task.status = AgentStatus.COMPLETED
            task.completed_at = datetime.utcnow()
            logger.info("Azure agent completed: %d insights", len(insights))

        except Exception as exc:
            logger.error("Azure agent failed: %s", exc)
            task.status = AgentStatus.FAILED
            task.error = str(exc)
            task.completed_at = datetime.utcnow()

        return task

    async def get_native_recommendations(self) -> list[dict[str, Any]]:
        """Fetch Azure Advisor recommendations."""
        try:
            settings = get_settings()
            if not settings.azure_subscription_id:
                return []

            connector = AzureCostConnector({
                "azure_subscription_id": settings.azure_subscription_id,
                "azure_tenant_id": settings.azure_tenant_id,
                "azure_client_id": settings.azure_client_id,
                "azure_client_secret": settings.azure_client_secret,
            })
            await connector.authenticate()
            recs = await connector.get_recommendations()
            await connector.close()
            return recs
        except Exception as exc:
            logger.warning("Azure native recommendations failed: %s", exc)
            return []

    def get_system_prompt(self) -> str:
        """Return system prompt for LLM-based reasoning."""
        return """You are the Azure Cost Specialist Agent for CloudSense.

Analyze Azure billing data to find optimization opportunities:
1. Look for idle VMs, unused disks, and empty App Service plans
2. Identify Hybrid Benefit gaps (running Windows/SQL without AHUB)
3. Check reservation coverage and recommend Azure Reservations
4. Flag orphaned resources (unattached disks, old snapshots)
5. Assess tag compliance for chargeback accuracy

Quantify all recommendations with projected savings and risk levels.
Prioritize high-impact, reversible actions."""
