"""
LangGraph Supervisor Agent — orchestrates all 8 specialist agents.

DAG:
  START → DISPATCH → (AWS | AZURE | GCP | ANOMALY | TAGGING) →
          SYNTHESIZE → POLICY_CHECK → END

Phase 4 additions:
  - Tagging agent node wired into the DAG
  - ActionAgent invoked post-approval (separate execute() call)
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import structlog
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from cloudsense.agents.shared_types import AgentState, CostInsight, RecommendationResult
from cloudsense.agents.specialist.aws_agent import AWSCostAgent
from cloudsense.agents.specialist.azure_agent import AzureCostAgent
from cloudsense.agents.specialist.gcp_agent import GCPCostAgent
from cloudsense.agents.specialist.anomaly_agent import AnomalyDetectionAgent
from cloudsense.agents.specialist.tagging_agent import TaggingAgent
from cloudsense.agents.tools.cost_tools import ClickHouseClient
from cloudsense.policy.engine import PolicyEngine
from cloudsense.services.api.config import Settings, get_settings

logger = structlog.get_logger()


class SupervisorAgent:
    """
    Supervisor that orchestrates the full Phase 1-4 agent fleet.

    Phase 4 adds:
      - TaggingAgent node (runs in parallel with cloud cost agents)
      - richer synthesis (dedup + sort + tagging insights merged)
      - policy check gate unchanged (OPA still gates all insights)
    """

    def __init__(
        self,
        clickhouse_client: ClickHouseClient,
        settings: Settings | None = None,
    ) -> None:
        self._ch = clickhouse_client
        self._settings = settings or get_settings()
        self._policy = PolicyEngine()
        self._graph = self._build_graph()

    def _build_graph(self) -> CompiledStateGraph:
        workflow = StateGraph(AgentState)

        # Nodes
        workflow.add_node("dispatch", self._node_dispatch)
        workflow.add_node("aws", self._node_aws)
        workflow.add_node("azure", self._node_azure)
        workflow.add_node("gcp", self._node_gcp)
        workflow.add_node("anomaly", self._node_anomaly)
        workflow.add_node("tagging", self._node_tagging)
        workflow.add_node("synthesize", self._node_synthesize)
        workflow.add_node("policy_check", self._node_policy_check)

        # Edges
        workflow.set_entry_point("dispatch")
        workflow.add_conditional_edges(
            "dispatch",
            self._router_dispatch,
            {
                "aws": "aws",
                "azure": "azure",
                "gcp": "gcp",
                "anomaly": "anomaly",
                "tagging": "tagging",
                "synthesize": "synthesize",
            },
        )
        workflow.add_edge("aws", "synthesize")
        workflow.add_edge("azure", "synthesize")
        workflow.add_edge("gcp", "synthesize")
        workflow.add_edge("anomaly", "synthesize")
        workflow.add_edge("tagging", "synthesize")
        workflow.add_edge("synthesize", "policy_check")
        workflow.add_edge("policy_check", END)

        return workflow.compile()

    async def analyze(
        self, goal: str, providers: list[str], time_range_days: int = 30
    ) -> RecommendationResult:
        logger.info("supervisor_analysis_start", goal=goal, providers=providers)
        initial_state = AgentState(
            goal=goal, providers=providers, time_range_days=time_range_days
        )
        final_state = await self._graph.ainvoke(initial_state)

        insights = final_state.get("insights", [])
        recs = final_state.get("recommendations", [])
        violations = recs[0].opa_policy_violations if recs else []

        rec = RecommendationResult(
            recommendation_id=str(uuid4()),
            goal=goal,
            insights=insights,
            total_projected_monthly_savings=sum(
                (i.projected_monthly_savings or 0) for i in insights
            ),
            total_projected_annual_savings=sum(
                (i.projected_annual_savings or 0) for i in insights
            ),
            total_affected_resources=sum(len(i.resource_ids) for i in insights),
            priority_score=0.85,
            execution_order=[i.insight_id for i in insights],
            opa_policy_violations=violations,
        )
        logger.info("supervisor_analysis_complete", recommendation_id=rec.recommendation_id,
                    insights=len(insights), savings=float(rec.total_projected_monthly_savings))
        return rec

    # ── Nodes ───────────────────────────────────────────────────

    async def _node_dispatch(self, state: AgentState) -> AgentState:
        logger.info("supervisor_dispatch", providers=state.providers)
        state.current_agent = "dispatch"
        return state

    def _router_dispatch(self, state: AgentState) -> str:
        """Route to next unexecuted agent in the pipeline."""
        completed = set(state.completed_agents)
        # Cloud cost agents
        for provider in state.providers:
            if provider not in completed:
                state.current_agent = provider
                return provider
        # Phase 3 — anomaly
        if "anomaly" not in completed:
            return "anomaly"
        # Phase 4 — tagging
        if "tagging" not in completed:
            return "tagging"
        return "synthesize"

    async def _node_aws(self, state: AgentState) -> AgentState:
        try:
            agent = AWSCostAgent(self._ch, self._settings)
            state.insights.extend(await agent.analyze(state.time_range_days))
        except Exception as exc:
            state.errors.append(f"aws_agent: {exc}")
            logger.error("aws_agent_failed", error=str(exc))
        state.completed_agents.append("aws")
        return state

    async def _node_azure(self, state: AgentState) -> AgentState:
        try:
            agent = AzureCostAgent(self._ch)
            state.insights.extend(await agent.analyze(state.time_range_days))
        except Exception as exc:
            state.errors.append(f"azure_agent: {exc}")
            logger.error("azure_agent_failed", error=str(exc))
        state.completed_agents.append("azure")
        return state

    async def _node_gcp(self, state: AgentState) -> AgentState:
        try:
            agent = GCPCostAgent(self._ch)
            state.insights.extend(await agent.analyze(state.time_range_days))
        except Exception as exc:
            state.errors.append(f"gcp_agent: {exc}")
            logger.error("gcp_agent_failed", error=str(exc))
        state.completed_agents.append("gcp")
        return state

    async def _node_anomaly(self, state: AgentState) -> AgentState:
        try:
            agent = AnomalyDetectionAgent(self._ch)
            state.insights.extend(await agent.analyze(state.time_range_days))
        except Exception as exc:
            state.errors.append(f"anomaly_agent: {exc}")
            logger.error("anomaly_agent_failed", error=str(exc))
        state.completed_agents.append("anomaly")
        return state

    async def _node_tagging(self, state: AgentState) -> AgentState:
        """Phase 4 — tagging compliance."""
        try:
            agent = TaggingAgent(self._ch, self._settings)
            state.insights.extend(await agent.analyze(state.time_range_days))
        except Exception as exc:
            state.errors.append(f"tagging_agent: {exc}")
            logger.error("tagging_agent_failed", error=str(exc))
        state.completed_agents.append("tagging")
        return state

    async def _node_synthesize(self, state: AgentState) -> AgentState:
        logger.info("supervisor_synthesize", insights=len(state.insights))
        # Dedup by (provider, resource_id, action_type)
        seen: set[str] = set()
        unique: list[CostInsight] = []
        for insight in state.insights:
            key = (
                f"{insight.provider}:"
                f"{insight.resource_ids[0] if insight.resource_ids else 'global'}:"
                f"{insight.action_type}"
            )
            if key not in seen:
                seen.add(key)
                unique.append(insight)
        # Sort by projected savings descending
        unique.sort(key=lambda x: x.projected_monthly_savings or 0, reverse=True)
        state.insights = unique
        state.completed_agents.append("synthesize")
        logger.info("supervisor_synthesize_complete", unique_insights=len(unique))
        return state

    async def _node_policy_check(self, state: AgentState) -> AgentState:
        logger.info("supervisor_policy_check", insights=len(state.insights))
        violations: list[str] = []
        for insight in state.insights:
            try:
                policy_result = await self._policy.evaluate(insight)
                if not policy_result.get("allowed", True):
                    violations.append(
                        f"{insight.insight_id}: {policy_result.get('reason', 'Policy denied')}"
                    )
            except Exception as exc:
                logger.warning("policy_check_failed", insight_id=insight.insight_id, error=str(exc))

        rec = RecommendationResult(
            recommendation_id=str(uuid4()),
            goal=state.goal,
            insights=state.insights,
            total_projected_monthly_savings=sum(
                (i.projected_monthly_savings or 0) for i in state.insights
            ),
            total_projected_annual_savings=sum(
                (i.projected_annual_savings or 0) for i in state.insights
            ),
            total_affected_resources=sum(len(i.resource_ids) for i in state.insights),
            priority_score=0.85,
            opa_policy_violations=violations,
            requires_approval=any(
                i.severity.value in ("critical", "high") for i in state.insights
            ),
        )
        state.recommendations.append(rec)
        state.completed_agents.append("policy_check")
        return state
