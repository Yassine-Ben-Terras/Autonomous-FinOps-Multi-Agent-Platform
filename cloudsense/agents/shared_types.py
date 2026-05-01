"""Shared Pydantic models for agent state."""
from __future__ import annotations
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field

class InsightSeverity(str, Enum):
    CRITICAL = "critical"; HIGH = "high"; MEDIUM = "medium"; LOW = "low"; INFO = "info"

class InsightStatus(str, Enum):
    OPEN = "open"; ACKNOWLEDGED = "acknowledged"; RESOLVED = "resolved"; DISMISSED = "dismissed"

class CostInsight(BaseModel):
    insight_id: str = Field(...)
    agent: str = Field(...)
    provider: str = Field(...)
    severity: InsightSeverity
    status: InsightStatus = InsightStatus.OPEN
    title: str = Field(..., max_length=200)
    description: str = Field(..., max_length=2000)
    resource_ids: list[str] = Field(default_factory=list)
    service_name: str | None = None
    region: str | None = None
    current_monthly_cost: Decimal | None = None
    projected_monthly_savings: Decimal | None = None
    projected_annual_savings: Decimal | None = None
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    recommendation: str | None = None
    action_type: str | None = None
    risk_level: str | None = None
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: datetime | None = None
    tags: dict[str, str] = Field(default_factory=dict)

class RecommendationResult(BaseModel):
    recommendation_id: str = Field(...)
    goal: str = Field(...)
    insights: list[CostInsight] = Field(default_factory=list)
    total_projected_monthly_savings: Decimal = Decimal("0")
    total_projected_annual_savings: Decimal = Decimal("0")
    total_affected_resources: int = 0
    priority_score: float = Field(..., ge=0.0, le=1.0)
    execution_order: list[str] = Field(default_factory=list)
    opa_policy_violations: list[str] = Field(default_factory=list)
    requires_approval: bool = True
    approved_by: str | None = None
    approved_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class AgentState(BaseModel):
    goal: str = Field(...)
    providers: list[str] = Field(default_factory=list)
    time_range_days: int = 30
    insights: list[CostInsight] = Field(default_factory=list)
    recommendations: list[RecommendationResult] = Field(default_factory=list)
    current_agent: str | None = None
    completed_agents: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    clickhouse_query_results: list[dict[str, Any]] = Field(default_factory=list)
    memory_context: str = ""
    class Config:
        arbitrary_types_allowed = True
