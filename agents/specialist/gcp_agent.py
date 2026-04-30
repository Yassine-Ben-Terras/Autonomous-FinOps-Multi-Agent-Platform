"""
GCP Cost Agent — Specialist sub-agent for GCP cost analysis.

Analyzes GCP billing data to detect:
- Idle Compute Engine instances and GKE node pools
- Over-provisioned resources
- Committed use discount (CUD) gaps
- Unused persistent disks
- BigQuery slot optimization
- Tag/label compliance
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
from connectors.gcp.cost_connector import GCPCostConnector
from services.api.config import get_settings

logger = logging.getLogger(__name__)


class GCPCostAgent:
    """GCP specialist cost analysis agent."""

    provider: str = "gcp"
    display_name: str = "GCP Cost Agent"

    def __init__(self) -> None:
        self.tools = CostAnalysisTools()

    async def execute(self, task: AgentTask) -> AgentTask:
        """Execute an analysis task and produce insights."""
        task.status = AgentStatus.RUNNING
        task.started_at = datetime.utcnow()
        logger.info("GCP agent executing task: %s", task.task_id)

        try:
            # Gather data
            spend_data = await self.tools.get_provider_spend("gcp", days=30)
            services = await self.tools.get_service_breakdown("gcp", days=30)
            idle = await self.tools.get_idle_resources("gcp", days=14)
            commitment = await self.tools.get_commitment_coverage("gcp", days=30)
            tags = await self.tools.get_tag_coverage("gcp", days=30)
            regions = await self.tools.get_cost_by_region("gcp", days=30)

            insights: list[CostInsight] = []

            # Overview
            insights.append(
                CostInsight(
                    provider="gcp",
                    category=RecommendationCategory.GENERAL,
                    title=f"GCP Monthly Spend: ${spend_data['total_cost']:,.2f}",
                    description=(
                        f"GCP spend for last {spend_data['period_days']} days: "
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
                        provider="gcp",
                        category=RecommendationCategory.GENERAL,
                        title=f"Top GCP Service: {top['service_name']} (${top['cost']:,.2f})",
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
                        provider="gcp",
                        category=RecommendationCategory.IDLE_RESOURCE,
                        title=f"{len(idle)} potentially idle GCP resources",
                        description=f"Idle resources costing ${total_cost:,.2f}/period",
                        current_monthly_cost=total_cost,
                        projected_monthly_savings=total_cost * 0.8,
                        confidence_score=0.75,
                        risk_level=RiskLevel.MEDIUM,
                        action_required=True,
                        suggested_action="Review and stop idle GCE instances",
                    )
                )

            # CUD gap
            if commitment["commitment_coverage_pct"] < 70:
                insights.append(
                    CostInsight(
                        provider="gcp",
                        category=RecommendationCategory.COMMITMENT_GAP,
                        title=f"CUD coverage: {commitment['commitment_coverage_pct']:.1f}%",
                        description=(
                            f"Committed Use Discount coverage is low. "
                            f"Opportunity: ${commitment['opportunity']:,.2f}"
                        ),
                        current_monthly_cost=commitment["total_effective_cost"],
                        projected_monthly_savings=commitment["opportunity"] * 0.25,
                        confidence_score=0.8,
                        risk_level=RiskLevel.LOW,
                        action_required=True,
                        suggested_action="Purchase 1-year or 3-year CUDs for stable workloads",
                    )
                )

            # Label compliance
            if tags["team_tag_coverage_pct"] < 80:
                insights.append(
                    CostInsight(
                        provider="gcp",
                        category=RecommendationCategory.TAG_COMPLIANCE,
                        title=f"GCP label compliance: {tags['team_tag_coverage_pct']:.1f}%",
                        description=f"{tags['untagged_records']} resources lack proper labels",
                        confidence_score=0.85,
                        risk_level=RiskLevel.LOW,
                        action_required=True,
                        suggested_action="Enforce labels via Organization Policy",
                    )
                )

            # Native recommendations
            native_recs = await self.get_native_recommendations()
            for rec in native_recs[:5]:
                cost_info = rec.get("estimated_cost", {})
                savings = 0.0
                if cost_info:
                    try:
                        savings = float(cost_info.get("units", 0))
                    except (ValueError, TypeError):
                        pass

                insights.append(
                    CostInsight(
                        provider="gcp",
                        category=RecommendationCategory.IDLE_RESOURCE,
                        title=f"GCP Recommender: {rec.get('description', 'Recommendation')[:80]}",
                        description=rec.get("description", ""),
                        projected_monthly_savings=savings,
                        confidence_score=0.7,
                        risk_level=RiskLevel.LOW,
                        action_required=True,
                        metadata={"source": "gcp_recommender", "priority": rec.get("priority"), "raw": rec},
                    )
                )

            task.result = insights
            task.status = AgentStatus.COMPLETED
            task.completed_at = datetime.utcnow()
            logger.info("GCP agent completed: %d insights", len(insights))

        except Exception as exc:
            logger.error("GCP agent failed: %s", exc)
            task.status = AgentStatus.FAILED
            task.error = str(exc)
            task.completed_at = datetime.utcnow()

        return task

    async def get_native_recommendations(self) -> list[dict[str, Any]]:
        """Fetch GCP Recommender API recommendations."""
        try:
            settings = get_settings()
            if not settings.gcp_project_id:
                return []

            connector = GCPCostConnector({
                "gcp_project_id": settings.gcp_project_id,
                "gcp_credentials_json": settings.gcp_credentials_json,
            })
            await connector.authenticate()
            recs = await connector.get_recommendations()
            await connector.close()
            return recs
        except Exception as exc:
            logger.warning("GCP native recommendations failed: %s", exc)
            return []

    def get_system_prompt(self) -> str:
        """Return system prompt for LLM-based reasoning."""
        return """You are the GCP Cost Specialist Agent for CloudSense.

Analyze GCP billing data to find optimization opportunities:
1. Identify idle GCE instances and underutilized GKE node pools
2. Find over-provisioned resources (disks, IPs, load balancers)
3. Check Committed Use Discount coverage and recommend CUD purchases
4. Flag unattached persistent disks and old snapshots
5. Assess BigQuery costs and recommend slot reservations
6. Check label compliance for showback/chargeback

Focus on reversible actions first. Quantify all recommendations."""
