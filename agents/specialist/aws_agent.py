"""
AWS Cost Agent — Specialist sub-agent for AWS cost analysis.

Analyzes AWS billing data to detect:
- Idle/unused EC2 instances and RDS databases
- Right-sizing opportunities
- Reserved Instance and Savings Plan gaps
- Orphaned resources (unattached volumes, old snapshots)
- Tag compliance issues

Uses LangGraph-compatible tool-calling with structured output.
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
from connectors.aws.cost_connector import AWSCostConnector
from services.api.config import get_settings

logger = logging.getLogger(__name__)


class AWSCostAgent:
    """AWS specialist cost analysis agent."""

    provider: str = "aws"
    display_name: str = "AWS Cost Agent"

    def __init__(self) -> None:
        self.tools = CostAnalysisTools()
        self._connector: AWSCostConnector | None = None

    async def execute(self, task: AgentTask) -> AgentTask:
        """Execute an analysis task and produce insights."""
        task.status = AgentStatus.RUNNING
        task.started_at = datetime.utcnow()
        logger.info("AWS agent executing task: %s", task.task_id)

        try:
            # Gather data using tools
            spend_data = await self.tools.get_provider_spend("aws", days=30)
            services = await self.tools.get_service_breakdown("aws", days=30)
            idle = await self.tools.get_idle_resources("aws", days=14)
            commitment = await self.tools.get_commitment_coverage("aws", days=30)
            tags = await self.tools.get_tag_coverage("aws", days=30)
            regions = await self.tools.get_cost_by_region("aws", days=30)

            # Generate insights from data
            insights: list[CostInsight] = []

            # Insight 1: Top spend overview
            insights.append(
                CostInsight(
                    provider="aws",
                    category=RecommendationCategory.GENERAL,
                    title=f"AWS Monthly Spend: ${spend_data['total_cost']:,.2f}",
                    description=(
                        f"AWS spend for last {spend_data['period_days']} days: "
                        f"${spend_data['total_cost']:,.2f} across {spend_data['service_count']} services. "
                        f"Savings from commitments: ${spend_data['total_savings']:,.2f}."
                    ),
                    current_monthly_cost=spend_data["total_cost"],
                    projected_monthly_savings=spend_data["total_savings"],
                    confidence_score=0.95,
                    risk_level=RiskLevel.LOW,
                )
            )

            # Insight 2: Top services
            if services:
                top = services[0]
                insights.append(
                    CostInsight(
                        provider="aws",
                        category=RecommendationCategory.GENERAL,
                        title=f"Top AWS Service: {top['service_name']} (${top['cost']:,.2f})",
                        description=(
                            f"Highest spend service is {top['service_name']} at ${top['cost']:,.2f}. "
                            f"Top 5 services: {', '.join(s['service_name'] for s in services[:5])}."
                        ),
                        current_monthly_cost=top["cost"],
                        confidence_score=0.9,
                        risk_level=RiskLevel.LOW,
                    )
                )

            # Insight 3: Idle resources
            if idle:
                total_idle_cost = sum(r["total_cost"] for r in idle)
                insights.append(
                    CostInsight(
                        provider="aws",
                        category=RecommendationCategory.IDLE_RESOURCE,
                        title=f"Found {len(idle)} potentially idle resources (${total_idle_cost:,.2f})",
                        description=(
                            f"Detected {len(idle)} resources with ongoing costs but minimal usage. "
                            f"Examples: {', '.join(r['resource_id'] for r in idle[:3])}."
                        ),
                        current_monthly_cost=total_idle_cost,
                        projected_monthly_savings=total_idle_cost * 0.8,
                        confidence_score=0.75,
                        risk_level=RiskLevel.MEDIUM,
                        action_required=True,
                        suggested_action="Review and stop/terminate idle resources after confirmation",
                    )
                )

            # Insight 4: Commitment gap
            if commitment["commitment_coverage_pct"] < 70:
                insights.append(
                    CostInsight(
                        provider="aws",
                        category=RecommendationCategory.COMMITMENT_GAP,
                        title=f"Commitment coverage only {commitment['commitment_coverage_pct']:.1f}%",
                        description=(
                            f"Only {commitment['commitment_coverage_pct']:.1f}% of spend is covered by "
                            f"RIs or Savings Plans. Opportunity: ${commitment['opportunity']:,.2f} "
                            f"could be committed for 20-40% savings."
                        ),
                        current_monthly_cost=commitment["total_effective_cost"],
                        projected_monthly_savings=commitment["opportunity"] * 0.3,
                        confidence_score=0.8,
                        risk_level=RiskLevel.LOW,
                        action_required=True,
                        suggested_action="Purchase Reserved Instances or Savings Plans for stable workloads",
                    )
                )

            # Insight 5: Tag compliance
            if tags["team_tag_coverage_pct"] < 80:
                insights.append(
                    CostInsight(
                        provider="aws",
                        category=RecommendationCategory.TAG_COMPLIANCE,
                        title=f"Tag compliance: {tags['team_tag_coverage_pct']:.1f}% have team tags",
                        description=(
                            f"Only {tags['team_tag_coverage_pct']:.1f}% of resources have team tags. "
                            f"{tags['untagged_records']} records are completely untagged. "
                            "This prevents accurate chargeback."
                        ),
                        confidence_score=0.85,
                        risk_level=RiskLevel.LOW,
                        action_required=True,
                        suggested_action="Enforce tagging policy via AWS Organizations or OPA",
                    )
                )

            # Insight 6: Regional distribution
            if len(regions) > 3:
                insights.append(
                    CostInsight(
                        provider="aws",
                        category=RecommendationCategory.GENERAL,
                        title=f"Resources spread across {len(regions)} regions",
                        description=(
                            f"Costs distributed across {len(regions)} regions. "
                            f"Top: {regions[0]['region']} (${regions[0]['cost']:,.2f}), "
                            f"{regions[1]['region']} (${regions[1]['cost']:,.2f})"
                        ),
                        confidence_score=0.9,
                        risk_level=RiskLevel.LOW,
                    )
                )

            # If resource inventory available, add deeper insights
            resource_insights = await self._analyze_resource_inventory()
            insights.extend(resource_insights)

            task.result = insights
            task.status = AgentStatus.COMPLETED
            task.completed_at = datetime.utcnow()
            logger.info("AWS agent completed: %d insights", len(insights))

        except Exception as exc:
            logger.error("AWS agent failed: %s", exc)
            task.status = AgentStatus.FAILED
            task.error = str(exc)
            task.completed_at = datetime.utcnow()

        return task

    async def _analyze_resource_inventory(self) -> list[CostInsight]:
        """Fetch and analyze live resource inventory for deeper insights."""
        insights: list[CostInsight] = []
        try:
            settings = get_settings()
            if not settings.aws_access_key_id:
                return insights

            connector = AWSCostConnector({
                "aws_access_key_id": settings.aws_access_key_id,
                "aws_secret_access_key": settings.aws_secret_access_key,
                "aws_default_region": settings.aws_default_region,
            })
            authenticated = await connector.authenticate()
            if not authenticated:
                return insights

            resources = await connector.get_resource_inventory()

            # Analyze EC2 instances
            ec2_instances = [r for r in resources if r.get("resource_type") == "ec2"]
            running = [r for r in ec2_instances if r.get("state") == "running"]
            stopped = [r for r in ec2_instances if r.get("state") == "stopped"]
            old_gen = [r for r in ec2_instances if r.get("waste_indicators", {}).get("old_generation")]

            if stopped:
                insights.append(
                    CostInsight(
                        provider="aws",
                        category=RecommendationCategory.IDLE_RESOURCE,
                        title=f"{len(stopped)} stopped EC2 instances may incur storage charges",
                        description=(
                            f"Found {len(stopped)} stopped EC2 instances. "
                            "Stopped instances don't incur compute charges but may still "
                            "incur EBS volume costs. Consider creating AMIs and terminating."
                        ),
                        projected_monthly_savings=len(stopped) * 50.0,  # Rough estimate
                        confidence_score=0.7,
                        risk_level=RiskLevel.LOW,
                        action_required=True,
                        suggested_action="Snapshot and terminate stopped instances after validation",
                    )
                )

            if old_gen:
                insights.append(
                    CostInsight(
                        provider="aws",
                        category=RecommendationCategory.RIGHT_SIZE,
                        title=f"{len(old_gen)} EC2 instances using older generation types",
                        description=(
                            f"Found {len(old_gen)} instances on older generation types "
                            f"({', '.join(set(r['instance_type'] for r in old_gen[:5]))}). "
                            "Migrating to current generation offers better price/performance."
                        ),
                        projected_monthly_savings=len(old_gen) * 30.0,
                        confidence_score=0.75,
                        risk_level=RiskLevel.MEDIUM,
                        action_required=True,
                        suggested_action="Plan migration to current generation instance families",
                    )
                )

            await connector.close()

        except Exception as exc:
            logger.warning("Resource inventory analysis failed: %s", exc)

        return insights

    async def get_native_recommendations(self) -> list[dict[str, Any]]:
        """Fetch AWS native recommendations (Trusted Advisor, Compute Optimizer)."""
        try:
            settings = get_settings()
            if not settings.aws_access_key_id:
                return []

            connector = AWSCostConnector({
                "aws_access_key_id": settings.aws_access_key_id,
                "aws_secret_access_key": settings.aws_secret_access_key,
                "aws_default_region": settings.aws_default_region,
            })
            await connector.authenticate()
            recs = await connector.get_recommendations()
            await connector.close()
            return recs
        except Exception as exc:
            logger.warning("Native recommendations fetch failed: %s", exc)
            return []

    def get_system_prompt(self) -> str:
        """Return the system prompt for LLM-based reasoning."""
        return """You are the AWS Cost Specialist Agent for CloudSense, an autonomous FinOps platform.

Your role is to analyze AWS billing data and identify cost optimization opportunities.
You have access to tools that query the ClickHouse OLAP store for AWS cost data.

When analyzing:
1. Always consider the full context — look at trends, not just point-in-time data
2. Quantify every recommendation with projected savings
3. Assess risk level carefully — production resources need more scrutiny
4. Consider commitment discounts (RI, Savings Plans) as primary levers
5. Flag tag compliance issues that prevent accurate chargeback

Your output must be structured CostInsight objects with:
- Clear title and description
- Quantified current_cost and projected_savings
- Confidence score (0-1)
- Risk level (low/medium/high/critical)
- Specific suggested_action when action is required

Be thorough but prioritize high-impact, low-risk recommendations first."""
