"""LangGraph Supervisor Agent.

Orchestrates specialist sub-agents via DAG:
START → DISPATCH → (AWS | AZURE | GCP) → SYNTHESIZE → POLICY_CHECK → END
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
from cloudsense.agents.tools.cost_tools import ClickHouseClient
from cloudsense.policy.engine import PolicyEngine
from cloudsense.services.api.config import Settings, get_settings

logger = structlog.get_logger()

class SupervisorAgent:
    def __init__(self, clickhouse_client: ClickHouseClient, settings: Settings | None = None) -> None:
        self._ch = clickhouse_client
        self._settings = settings or get_settings()
        self._policy = PolicyEngine()
        self._graph = self._build_graph()

    def _build_graph(self) -> CompiledStateGraph:
        workflow = StateGraph(AgentState)
        workflow.add_node("dispatch", self._node_dispatch)
        workflow.add_node("aws", self._node_aws)
        workflow.add_node("azure", self._node_azure)
        workflow.add_node("gcp", self._node_gcp)
        workflow.add_node("synthesize", self._node_synthesize)
        workflow.add_node("policy_check", self._node_policy_check)
        workflow.set_entry_point("dispatch")
        workflow.add_conditional_edges("dispatch", self._router_dispatch,
            {"aws": "aws", "azure": "azure", "gcp": "gcp", "synthesize": "synthesize"})
        workflow.add_edge("aws", "synthesize")
        workflow.add_edge("azure", "synthesize")
        workflow.add_edge("gcp", "synthesize")
        workflow.add_edge("synthesize", "policy_check")
        workflow.add_edge("policy_check", END)
        return workflow.compile()

    async def analyze(self, goal: str, providers: list[str], time_range_days: int = 30) -> RecommendationResult:
        logger.info("supervisor_analysis_start", goal=goal, providers=providers)
        initial_state = AgentState(goal=goal, providers=providers, time_range_days=time_range_days)
        final_state = await self._graph.ainvoke(initial_state)
        rec = RecommendationResult(
            recommendation_id=str(uuid4()), goal=goal,
            insights=final_state.get("insights", []),
            total_projected_monthly_savings=sum((i.projected_monthly_savings or 0) for i in final_state.get("insights", [])),
            total_projected_annual_savings=sum((i.projected_annual_savings or 0) for i in final_state.get("insights", [])),
            total_affected_resources=sum(len(i.resource_ids) for i in final_state.get("insights", [])),
            priority_score=0.85,
            execution_order=[i.insight_id for i in final_state.get("insights", [])],
            opa_policy_violations=final_state.get("recommendations", [{}])[0].get("opa_policy_violations", []) if final_state.get("recommendations") else [],
        )
        logger.info("supervisor_analysis_complete", recommendation_id=rec.recommendation_id)
        return rec

    async def _node_dispatch(self, state: AgentState) -> AgentState:
        logger.info("supervisor_dispatch", providers=state.providers)
        state.current_agent = "dispatch"
        return state

    def _router_dispatch(self, state: AgentState) -> str:
        completed = set(state.completed_agents)
        for provider in state.providers:
            if provider not in completed:
                state.current_agent = provider
                return provider
        return "synthesize"

    async def _node_aws(self, state: AgentState) -> AgentState:
        agent = AWSCostAgent(self._ch, self._settings)
        state.insights.extend(await agent.analyze(state.time_range_days))
        state.completed_agents.append("aws")
        return state

    async def _node_azure(self, state: AgentState) -> AgentState:
        agent = AzureCostAgent(self._ch)
        state.insights.extend(await agent.analyze(state.time_range_days))
        state.completed_agents.append("azure")
        return state

    async def _node_gcp(self, state: AgentState) -> AgentState:
        agent = GCPCostAgent(self._ch)
        state.insights.extend(await agent.analyze(state.time_range_days))
        state.completed_agents.append("gcp")
        return state

    async def _node_synthesize(self, state: AgentState) -> AgentState:
        logger.info("supervisor_synthesize", insights=len(state.insights))
        seen: set[str] = set()
        unique: list[CostInsight] = []
        for insight in state.insights:
            key = f"{insight.provider}:{insight.resource_ids[0] if insight.resource_ids else ''}:{insight.action_type}"
            if key not in seen:
                seen.add(key)
                unique.append(insight)
        unique.sort(key=lambda x: x.projected_monthly_savings or 0, reverse=True)
        state.insights = unique
        state.completed_agents.append("synthesize")
        return state

    async def _node_policy_check(self, state: AgentState) -> AgentState:
        logger.info("supervisor_policy_check")
        violations = []
        for insight in state.insights:
            policy_result = await self._policy.evaluate(insight)
            if not policy_result.get("allowed", True):
                violations.append(f"{insight.insight_id}: {policy_result.get('reason', 'Policy denied')}")
        rec = RecommendationResult(
            recommendation_id=str(uuid4()), goal=state.goal, insights=state.insights,
            opa_policy_violations=violations,
            requires_approval=len(violations) == 0 and any(i.severity.value in ("critical", "high") for i in state.insights))
        state.recommendations.append(rec)
        state.completed_agents.append("policy_check")
        return state
