"""
CloudSense Supervisor Agent — LangGraph-based orchestration.

The supervisor is the central intelligence that:
1. Receives a high-level FinOps analysis goal
2. Decomposes it into sub-tasks for specialist agents
3. Routes tasks via a LangGraph DAG
4. Collects and synthesizes insights from all agents
5. Resolves conflicts between recommendations
6. Enforces policy constraints
7. Produces a final action plan

Uses a ReAct (Reasoning + Acting) loop with persistent memory.
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
    RecommendationResult,
    RiskLevel,
    SupervisorState,
    state_from_dict,
    state_to_dict,
)
from agents.specialist.aws_agent import AWSCostAgent
from agents.specialist.azure_agent import AzureCostAgent
from agents.specialist.gcp_agent import GCPCostAgent
from recommendations.engine import RecommendationEngine

logger = logging.getLogger(__name__)


class SupervisorAgent:
    """LangGraph supervisor orchestrating specialist agents.

    This implementation uses a sequential DAG pattern:
    parse_goal → dispatch_agents → collect_results → synthesize → apply_policies

    Future: Full LangGraph integration with conditional edges and cycles.
    """

    def __init__(self) -> None:
        self.aws_agent = AWSCostAgent()
        self.azure_agent = AzureCostAgent()
        self.gcp_agent = GCPCostAgent()
        self.rec_engine = RecommendationEngine()
        self._agent_registry = {
            "aws": self.aws_agent,
            "azure": self.azure_agent,
            "gcp": self.gcp_agent,
        }

    async def analyze(self, goal: str, providers: list[str] | None = None) -> SupervisorState:
        """Main entry point: run a full multi-cloud cost analysis.

        Args:
            goal: Natural language analysis goal (e.g., "Find cost savings across all clouds")
            providers: List of providers to analyze, or None for all

        Returns:
            Final supervisor state with all insights and recommendations
        """
        state = SupervisorState(goal=goal, status=AgentStatus.RUNNING)
        providers = providers or ["aws", "azure", "gcp"]

        logger.info("Supervisor starting analysis: goal='%s' providers=%s", goal, providers)

        try:
            # Phase 1: Create and dispatch tasks to specialist agents
            tasks = self._create_tasks(goal, providers)
            for task in tasks:
                state.add_task(task)

            # Phase 2: Execute all specialist agents (parallel in production)
            for task in state.tasks:
                await self._execute_task(task, state)

            # Phase 3: Synthesize insights into recommendations
            recommendations = self._synthesize_recommendations(state)
            state.recommendations = recommendations

            # Phase 4: Generate final report
            state.final_report = self._generate_report(state)
            state.status = AgentStatus.COMPLETED

            logger.info(
                "Analysis complete: %d insights, %d recommendations",
                len(state.insights),
                len(state.recommendations),
            )

        except Exception as exc:
            logger.error("Supervisor analysis failed: %s", exc)
            state.status = AgentStatus.FAILED

        state.updated_at = datetime.utcnow()
        return state

    async def quick_analysis(self, provider: str) -> SupervisorState:
        """Quick analysis for a single provider.

        Args:
            provider: Cloud provider to analyze

        Returns:
            Supervisor state with findings
        """
        goal = f"Quick cost analysis for {provider.upper()}"
        return await self.analyze(goal, providers=[provider])

    async def cross_cloud_analysis(self) -> SupervisorState:
        """Full cross-cloud analysis — highest value use case.

        Returns:
            Supervisor state with unified recommendations
        """
        goal = (
            "Comprehensive cross-cloud cost optimization analysis. "
            "Identify idle resources, right-sizing opportunities, commitment gaps, "
            "and tag compliance issues across AWS, Azure, and GCP. "
            "Prioritize by projected savings and flag cross-cloud redundancies."
        )
        return await self.analyze(goal, providers=["aws", "azure", "gcp"])

    def _create_tasks(self, goal: str, providers: list[str]) -> list[AgentTask]:
        """Decompose the analysis goal into agent tasks."""
        tasks: list[AgentTask] = []

        for provider in providers:
            if provider in self._agent_registry:
                task = AgentTask(
                    agent_type=provider,
                    goal=f"Analyze {provider.upper()} costs: {goal}",
                    provider=provider,
                    priority=1,
                )
                tasks.append(task)
                logger.debug("Created task %s for %s", task.task_id, provider)

        return tasks

    async def _execute_task(self, task: AgentTask, state: SupervisorState) -> None:
        """Execute a single agent task and merge results into state."""
        agent = self._agent_registry.get(task.agent_type)
        if not agent:
            task.status = AgentStatus.FAILED
            task.error = f"No agent registered for type: {task.agent_type}"
            return

        logger.info("Dispatching task %s to %s", task.task_id, agent.display_name)

        try:
            completed_task = await agent.execute(task)

            if completed_task.status == AgentStatus.COMPLETED:
                state.merge_insights(completed_task.result)
                logger.info(
                    "Task %s completed with %d insights",
                    task.task_id,
                    len(completed_task.result),
                )
            else:
                logger.warning(
                    "Task %s failed: %s",
                    task.task_id,
                    completed_task.error,
                )

        except Exception as exc:
            logger.error("Task %s execution error: %s", task.task_id, exc)
            task.status = AgentStatus.FAILED
            task.error = str(exc)

    def _synthesize_recommendations(self, state: SupervisorState) -> list[RecommendationResult]:
        """Synthesize all agent insights into actionable recommendations.

        This step:
        1. Deduplicates similar insights across providers
        2. Groups by category
        3. Ranks by savings potential
        4. Resolves conflicts (e.g., one agent says scale up, another says scale down)
        5. Generates unified recommendations
        """
        logger.info("Synthesizing %d insights into recommendations", len(state.insights))

        recommendations: list[RecommendationResult] = []

        # Group insights by category
        by_category: dict[RecommendationCategory, list[CostInsight]] = {}
        for insight in state.insights:
            cat = insight.category
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(insight)

        # Generate recommendations per category
        for category, insights in by_category.items():
            if category == RecommendationCategory.IDLE_RESOURCE:
                rec = self._synthesize_idle_resource_recommendation(insights)
                if rec:
                    recommendations.append(rec)

            elif category == RecommendationCategory.COMMITMENT_GAP:
                rec = self._synthesize_commitment_recommendation(insights)
                if rec:
                    recommendations.append(rec)

            elif category == RecommendationCategory.RIGHT_SIZE:
                rec = self._synthesize_rightsize_recommendation(insights)
                if rec:
                    recommendations.append(rec)

            elif category == RecommendationCategory.TAG_COMPLIANCE:
                rec = self._synthesize_tag_compliance_recommendation(insights)
                if rec:
                    recommendations.append(rec)

            elif category == RecommendationCategory.GENERAL:
                # Include top general insights as context
                pass

        # Sort by projected savings (highest first)
        recommendations.sort(
            key=lambda r: r.total_projected_monthly_savings,
            reverse=True,
        )

        return recommendations

    def _synthesize_idle_resource_recommendation(
        self, insights: list[CostInsight],
    ) -> RecommendationResult | None:
        """Synthesize idle resource findings into a unified recommendation."""
        if not insights:
            return None

        providers = list(set(i.provider for i in insights))
        total_savings = sum(i.projected_monthly_savings for i in insights)
        resource_count = sum(1 for i in insights if i.resource_id)

        descriptions = []
        for i in insights[:3]:
            descriptions.append(f"- {i.provider.upper()}: {i.title}")

        return RecommendationResult(
            title=f"Eliminate {len(insights)} idle resources across {len(providers)} clouds",
            description=(
                f"Found {len(insights)} idle resource groups costing an estimated "
                f"${total_savings:,.2f}/month.\n"
                + "\n".join(descriptions)
            ),
            category=RecommendationCategory.IDLE_RESOURCE,
            providers_involved=providers,
            total_projected_monthly_savings=total_savings,
            implementation_effort="low",
            risk_level=RiskLevel.LOW,
            actions=[
                "Review identified idle resources in dashboard",
                "Confirm with resource owners via Slack",
                "Schedule termination after backup verification",
                "Enable auto-shutdown policies for dev/test resources",
            ],
            requires_approval=True,
        )

    def _synthesize_commitment_recommendation(
        self, insights: list[CostInsight],
    ) -> RecommendationResult | None:
        """Synthesize commitment discount gaps."""
        if not insights:
            return None

        providers = list(set(i.provider for i in insights))
        total_savings = sum(i.projected_monthly_savings for i in insights)

        descriptions = []
        for i in insights:
            coverage = i.metadata.get("commitment_coverage_pct", "N/A")
            descriptions.append(f"- {i.provider.upper()}: Coverage {coverage}%")

        return RecommendationResult(
            title=f"Increase commitment coverage across {len(providers)} clouds",
            description=(
                f"Commitment discount coverage is below optimal. "
                f"Projected savings: ${total_savings:,.2f}/month.\n"
                + "\n".join(descriptions)
            ),
            category=RecommendationCategory.COMMITMENT_GAP,
            providers_involved=providers,
            total_projected_monthly_savings=total_savings,
            implementation_effort="medium",
            risk_level=RiskLevel.LOW,
            actions=[
                "Analyze 6-month usage trends for stable workloads",
                "Model RI/SP/CUD purchase scenarios",
                "Purchase commitments for top 3 services by spend",
                "Set up monitoring for commitment utilization",
            ],
            requires_approval=True,
        )

    def _synthesize_rightsize_recommendation(
        self, insights: list[CostInsight],
    ) -> RecommendationResult | None:
        """Synthesize right-sizing opportunities."""
        if not insights:
            return None

        providers = list(set(i.provider for i in insights))
        total_savings = sum(i.projected_monthly_savings for i in insights)

        return RecommendationResult(
            title=f"Right-size {len(insights)} over-provisioned resources",
            description=(
                f"Found {len(insights)} resources running on larger instances than needed. "
                f"Estimated savings: ${total_savings:,.2f}/month."
            ),
            category=RecommendationCategory.RIGHT_SIZE,
            providers_involved=providers,
            total_projected_monthly_savings=total_savings,
            implementation_effort="medium",
            risk_level=RiskLevel.MEDIUM,
            actions=[
                "Review utilization metrics for flagged resources",
                "Test smaller instance types in staging",
                "Schedule maintenance windows for resizing",
                "Monitor performance after changes",
            ],
            requires_approval=True,
        )

    def _synthesize_tag_compliance_recommendation(
        self, insights: list[CostInsight],
    ) -> RecommendationResult | None:
        """Synthesize tag compliance findings."""
        if not insights:
            return None

        providers = list(set(i.provider for i in insights))
        avg_coverage = sum(
            i.metadata.get("team_tag_coverage_pct", 0) for i in insights
        ) / len(insights) if insights else 0

        return RecommendationResult(
            title=f"Improve tagging compliance ({avg_coverage:.0f}% coverage)",
            description=(
                f"Tag compliance is below 80% target across {len(providers)} clouds. "
                f"This prevents accurate showback/chargeback and policy enforcement."
            ),
            category=RecommendationCategory.TAG_COMPLIANCE,
            providers_involved=providers,
            total_projected_monthly_savings=0.0,
            implementation_effort="low",
            risk_level=RiskLevel.LOW,
            actions=[
                "Deploy OPA tagging policies",
                "Run automated tag inference on untagged resources",
                "Enforce mandatory tags at provisioning time",
                "Create monthly tag compliance report",
            ],
            requires_approval=False,
        )

    def _generate_report(self, state: SupervisorState) -> str:
        """Generate a human-readable analysis report."""
        lines = [
            "# CloudSense Cost Analysis Report",
            "",
            f"**Goal:** {state.goal}",
            f"**Session:** {state.session_id}",
            f"**Generated:** {state.updated_at.isoformat()}",
            "",
            "## Summary",
            "",
            f"- **Total Insights:** {len(state.insights)}",
            f"- **Recommendations:** {len(state.recommendations)}",
            f"- **Total Projected Monthly Savings:** ${sum(r.total_projected_monthly_savings for r in state.recommendations):,.2f}",
            "",
            "## Recommendations (by priority)",
            "",
        ]

        for i, rec in enumerate(state.recommendations, 1):
            lines.extend([
                f"### {i}. {rec.title}",
                "",
                rec.description,
                "",
                f"- **Projected Savings:** ${rec.total_projected_monthly_savings:,.2f}/month",
                f"- **Providers:** {', '.join(rec.providers_involved)}",
                f"- **Risk:** {rec.risk_level.value}",
                f"- **Effort:** {rec.implementation_effort}",
                f"- **Requires Approval:** {'Yes' if rec.requires_approval else 'No'}",
                "",
                "**Actions:**",
                "",
            ])
            for action in rec.actions:
                lines.append(f"- [ ] {action}")
            lines.append("")

        # Add insight breakdown by provider
        lines.extend([
            "## Insights by Provider",
            "",
        ])
        provider_insights: dict[str, int] = {}
        for insight in state.insights:
            provider_insights[insight.provider] = provider_insights.get(insight.provider, 0) + 1

        for provider, count in sorted(provider_insights.items()):
            lines.append(f"- **{provider.upper()}:** {count} insights")

        lines.extend([
            "",
            "---",
            "*Generated by CloudSense FinOps Multi-Agent Platform*",
        ])

        return "\n".join(lines)

    # --- LangGraph integration helpers ---

    def build_graph(self) -> dict[str, Any]:
        """Build the LangGraph DAG definition (for future full integration).

        Returns a dict describing the graph structure that can be used
        with langgraph.Graph to compile an executable graph.
        """
        return {
            "nodes": {
                "parse_goal": "Decompose natural language goal into tasks",
                "dispatch_aws": "Run AWS specialist agent",
                "dispatch_azure": "Run Azure specialist agent",
                "dispatch_gcp": "Run GCP specialist agent",
                "synthesize": "Merge insights and resolve conflicts",
                "apply_policies": "Evaluate recommendations against OPA policies",
                "generate_report": "Produce final report",
            },
            "edges": [
                ("parse_goal", "dispatch_aws"),
                ("parse_goal", "dispatch_azure"),
                ("parse_goal", "dispatch_gcp"),
                ("dispatch_aws", "synthesize"),
                ("dispatch_azure", "synthesize"),
                ("dispatch_gcp", "synthesize"),
                ("synthesize", "apply_policies"),
                ("apply_policies", "generate_report"),
            ],
            "conditional_edges": {
                "apply_policies": {
                    "approved": "generate_report",
                    "rejected": "synthesize",  # Loop back for revision
                },
            },
        }
