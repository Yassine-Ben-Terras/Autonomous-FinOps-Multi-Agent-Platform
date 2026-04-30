"""
CloudSense Agent Shared Types — Pydantic v2 models for agent state,
recommendations, and inter-agent communication.

All models are serializable for LangGraph state persistence and Redis caching.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentStatus(str, Enum):
    """Execution status for agents and recommendations."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    WAITING_APPROVAL = "waiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


class RiskLevel(str, Enum):
    """Risk assessment for autonomous actions."""

    LOW = "low"        # Safe: read-only, no impact
    MEDIUM = "medium"  # Caution: may affect non-prod
    HIGH = "high"      # Risky: affects production
    CRITICAL = "critical"  # Dangerous: irreversible or widespread


class RecommendationCategory(str, Enum):
    """Categories of cost optimization recommendations."""

    IDLE_RESOURCE = "idle_resource"
    RIGHT_SIZE = "right_size"
    COMMITMENT_GAP = "commitment_gap"
    ORPHANED_RESOURCE = "orphaned_resource"
    TAG_COMPLIANCE = "tag_compliance"
    STORAGE_OPTIMIZATION = "storage_optimization"
    NETWORK_OPTIMIZATION = "network_optimization"
    RESERVED_INSTANCE = "reserved_instance"
    SAVINGS_PLAN = "savings_plan"
    GENERAL = "general"


class CostInsight(BaseModel):
    """A single cost insight produced by a specialist agent."""

    model_config = ConfigDict(populate_by_name=True)

    insight_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    provider: str = Field(..., description="Cloud provider: aws | azure | gcp")
    category: RecommendationCategory
    title: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    resource_id: str | None = None
    resource_type: str | None = None
    region: str | None = None
    current_monthly_cost: float = Field(default=0.0, ge=0)
    projected_monthly_savings: float = Field(default=0.0, ge=0)
    confidence_score: float = Field(default=0.8, ge=0, le=1)
    risk_level: RiskLevel = RiskLevel.MEDIUM
    impact: str | None = None  # human-readable impact statement
    action_required: bool = False
    suggested_action: str | None = None
    terraform_snippet: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AgentTask(BaseModel):
    """A task assigned by the supervisor to a specialist agent."""

    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_type: str = Field(..., description="Target agent: aws | azure | gcp | anomaly | forecast | tag | action | report")
    goal: str = Field(..., min_length=1, description="Natural language goal for the agent")
    provider: str | None = None
    date_range: tuple[str, str] | None = None  # (start, end) ISO dates
    filters: dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=1, ge=1, le=5)
    status: AgentStatus = AgentStatus.PENDING
    result: list[CostInsight] = Field(default_factory=list)
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class SupervisorState(BaseModel):
    """LangGraph state for the supervisor agent.

    This is the central state object that persists across the DAG execution.
    LangGraph uses this to pass context between nodes.
    """

    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    goal: str = Field(default="", description="High-level FinOps analysis goal")
    tasks: list[AgentTask] = Field(default_factory=list)
    insights: list[CostInsight] = Field(default_factory=list)
    recommendations: list[RecommendationResult] = Field(default_factory=list)
    policy_decisions: list[PolicyDecision] = Field(default_factory=list)
    final_report: str | None = None
    status: AgentStatus = AgentStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def add_task(self, task: AgentTask) -> None:
        self.tasks.append(task)
        self.updated_at = datetime.utcnow()

    def add_insight(self, insight: CostInsight) -> None:
        self.insights.append(insight)
        self.updated_at = datetime.utcnow()

    def merge_insights(self, new_insights: list[CostInsight]) -> None:
        self.insights.extend(new_insights)
        self.updated_at = datetime.utcnow()


class RecommendationResult(BaseModel):
    """Final recommendation after supervisor synthesis."""

    model_config = ConfigDict(populate_by_name=True)

    recommendation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    description: str
    category: RecommendationCategory
    providers_involved: list[str] = Field(default_factory=list)
    total_projected_monthly_savings: float = Field(default=0.0, ge=0)
    implementation_effort: str = "medium"  # low | medium | high
    risk_level: RiskLevel = RiskLevel.MEDIUM
    actions: list[str] = Field(default_factory=list)
    requires_approval: bool = True
    approval_status: AgentStatus = AgentStatus.WAITING_APPROVAL
    terraform_plan: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PolicyDecision(BaseModel):
    """Result of OPA policy evaluation."""

    model_config = ConfigDict(populate_by_name=True)

    decision_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    recommendation_id: str
    action: str
    allowed: bool
    reason: str | None = None
    policy_name: str | None = None
    evaluated_at: datetime = Field(default_factory=datetime.utcnow)


class AgentMessage(BaseModel):
    """Inter-agent communication message."""

    model_config = ConfigDict(populate_by_name=True)

    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    from_agent: str
    to_agent: str
    message_type: str  # task_assignment | insight | conflict | approval_request | status
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# LangGraph-compatible state dict helpers

def state_to_dict(state: SupervisorState) -> dict[str, Any]:
    """Convert supervisor state to a plain dict for LangGraph."""
    return state.model_dump(mode="json")


def state_from_dict(data: dict[str, Any]) -> SupervisorState:
    """Reconstruct supervisor state from a LangGraph state dict."""
    return SupervisorState(**data)
