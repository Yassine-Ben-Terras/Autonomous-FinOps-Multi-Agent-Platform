"""
CloudSense — Billing Domain Models
Pure data models for resources, recommendations, actions, anomalies, forecasts.
No I/O — fully unit-testable without any external service.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from cloudsense.core.models.enums import (
    ActionStatus,
    AgentName,
    CloudProvider,
    Environment,
    ResourceStatus,
)


class CloudResource(BaseModel):
    """A cloud resource tracked by CloudSense."""

    id: UUID = Field(default_factory=uuid4)
    provider: CloudProvider
    resource_id: str = Field(..., min_length=1, max_length=1024)
    resource_name: str | None = None
    resource_type: str
    region_id: str | None = None
    billing_account_id: str
    sub_account_id: str | None = None
    environment: Environment | None = None
    status: ResourceStatus = ResourceStatus.UNKNOWN
    tags: dict[str, str] = Field(default_factory=dict)
    monthly_cost: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    last_seen_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": True}


class CostRecommendation(BaseModel):
    """A cost-saving recommendation produced by a specialist agent."""

    id: UUID = Field(default_factory=uuid4)
    agent: AgentName
    provider: CloudProvider
    resource_id: str | None = None
    resource_type: str | None = None
    title: str = Field(..., min_length=1, max_length=256)
    description: str = Field(..., min_length=1)
    estimated_monthly_savings: Decimal = Field(..., ge=Decimal("0"))
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    effort_level: str = Field(default="medium")
    action_required: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def expiry_after_creation(self) -> "CostRecommendation":
        if self.expires_at and self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        return self

    @property
    def annual_savings(self) -> Decimal:
        return self.estimated_monthly_savings * 12

    model_config = {"frozen": True}


class ActionRequest(BaseModel):
    """A request for the Action Agent — requires OPA approval before execution."""

    id: UUID = Field(default_factory=uuid4)
    recommendation_id: UUID
    agent: AgentName = AgentName.ACTION
    provider: CloudProvider
    environment: Environment
    action_type: str = Field(..., description="e.g. rightsize, stop_instance, purchase_ri")
    target_resource_id: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    rollback_plan: dict[str, Any] = Field(default_factory=dict)
    status: ActionStatus = ActionStatus.PENDING
    requested_by: str
    approved_by: str | None = None
    rejected_reason: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    executed_at: datetime | None = None
    rollback_available_until: datetime | None = None

    @property
    def requires_human_approval(self) -> bool:
        """Production environments always require explicit human approval."""
        return self.environment == Environment.PRODUCTION

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            ActionStatus.COMPLETED,
            ActionStatus.FAILED,
            ActionStatus.ROLLED_BACK,
            ActionStatus.REJECTED,
        )

    model_config = {"frozen": True}


class CostAnomaly(BaseModel):
    """A detected billing spike produced by the Anomaly Agent."""

    id: UUID = Field(default_factory=uuid4)
    provider: CloudProvider
    billing_account_id: str
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    period_start: datetime
    period_end: datetime
    service_name: str | None = None
    region_id: str | None = None
    resource_id: str | None = None
    expected_cost: Decimal = Field(..., ge=Decimal("0"))
    actual_cost: Decimal = Field(..., ge=Decimal("0"))
    anomaly_score: float = Field(..., ge=0.0, le=1.0)
    root_cause: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)

    @property
    def cost_delta(self) -> Decimal:
        return self.actual_cost - self.expected_cost

    @property
    def percentage_increase(self) -> Decimal:
        if self.expected_cost == Decimal("0"):
            return Decimal("100")
        return (self.cost_delta / self.expected_cost * 100).quantize(Decimal("0.01"))

    @property
    def is_significant(self) -> bool:
        """True if anomaly exceeds 20% above expected and score > 0.5."""
        return self.percentage_increase > Decimal("20") and self.anomaly_score > 0.5

    model_config = {"frozen": True}


class CostForecast(BaseModel):
    """A 30/60/90-day cost projection produced by the Forecasting Agent."""

    id: UUID = Field(default_factory=uuid4)
    provider: CloudProvider | None = None
    billing_account_id: str | None = None
    service_name: str | None = None
    region_id: str | None = None
    team: str | None = None
    forecast_period_days: int = Field(..., ge=1, le=365)
    forecast_start: datetime
    forecast_end: datetime
    predicted_cost: Decimal = Field(..., ge=Decimal("0"))
    lower_bound: Decimal = Field(..., ge=Decimal("0"))
    upper_bound: Decimal = Field(..., ge=Decimal("0"))
    model_name: str = Field(default="prophet")
    model_version: str | None = None
    confidence_level: float = Field(default=0.95, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    budget_limit: Decimal | None = None

    @model_validator(mode="after")
    def bounds_order(self) -> "CostForecast":
        if self.lower_bound > self.predicted_cost:
            raise ValueError("lower_bound cannot exceed predicted_cost")
        if self.upper_bound < self.predicted_cost:
            raise ValueError("upper_bound cannot be less than predicted_cost")
        return self

    @property
    def budget_breach_risk(self) -> bool:
        if self.budget_limit is None:
            return False
        return self.upper_bound > self.budget_limit

    @property
    def confidence_range(self) -> Decimal:
        return self.upper_bound - self.lower_bound

    model_config = {"frozen": True}


class TagViolation(BaseModel):
    """A tag compliance violation found by the Tagging Agent."""

    id: UUID = Field(default_factory=uuid4)
    provider: CloudProvider
    resource_id: str
    resource_type: str | None = None
    billing_account_id: str
    missing_tags: list[str] = Field(default_factory=list)
    non_compliant_tags: dict[str, str] = Field(default_factory=dict)
    inferred_tags: dict[str, str] = Field(default_factory=dict)
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    monthly_cost_at_risk: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))

    @property
    def severity(self) -> str:
        total = len(self.missing_tags) + len(self.non_compliant_tags)
        if total == 0:
            return "none"
        if total <= 2:
            return "low"
        if total <= 5:
            return "medium"
        return "high"

    model_config = {"frozen": True}
