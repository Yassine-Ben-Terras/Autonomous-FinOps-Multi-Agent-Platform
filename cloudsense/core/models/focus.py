"""
CloudSense — FinOps FOCUS 1.0 Schema
Pydantic v2 models for the normalized billing data model.

Reference: https://focus.finops.org/
Pure data containers — no I/O, no DB, no network.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from cloudsense.core.models.enums import (
    ChargeCategory,
    ChargeFrequency,
    CloudProvider,
    CommitmentDiscountType,
)


class FocusRecord(BaseModel):
    """
    A single normalized billing record conforming to FinOps FOCUS 1.0.
    Every cloud provider raw billing row is transformed into this model
    by the ETL pipeline before being stored in ClickHouse.
    """

    # Identity
    id: UUID = Field(default_factory=uuid4)
    provider: CloudProvider

    # FOCUS 1.0 Core Dimensions
    billing_account_id: str = Field(..., min_length=1, max_length=256)
    billing_account_name: str | None = Field(default=None, max_length=512)
    resource_id: str | None = Field(default=None, max_length=1024)
    resource_name: str | None = Field(default=None, max_length=512)
    resource_type: str | None = Field(default=None, max_length=256)
    service_name: str = Field(..., min_length=1, max_length=256)
    service_category: str | None = Field(default=None, max_length=128)
    region_id: str | None = Field(default=None, max_length=128)
    availability_zone: str | None = Field(default=None, max_length=128)

    # Time Dimensions
    billing_period_start: datetime
    billing_period_end: datetime
    charge_period_start: datetime
    charge_period_end: datetime

    # Cost Dimensions (USD)
    effective_cost: Decimal
    list_cost: Decimal
    billed_cost: Decimal
    contracted_cost: Decimal | None = None
    currency: str = Field(default="USD", min_length=3, max_length=3)

    # Usage Dimensions
    usage_quantity: Decimal | None = None
    usage_unit: str | None = Field(default=None, max_length=64)

    # Charge Classification
    charge_category: ChargeCategory
    charge_class: str | None = Field(default=None, max_length=64)
    charge_frequency: ChargeFrequency | None = None
    charge_description: str | None = Field(default=None, max_length=1024)

    # Commitment Discounts
    commitment_discount_id: str | None = Field(default=None, max_length=512)
    commitment_discount_type: CommitmentDiscountType | None = None
    commitment_discount_name: str | None = Field(default=None, max_length=512)

    # Tags
    tags: dict[str, str] = Field(default_factory=dict)

    # Sub-account / Project
    sub_account_id: str | None = Field(default=None, max_length=256)
    sub_account_name: str | None = Field(default=None, max_length=512)

    # CloudSense Metadata
    ingested_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("currency")
    @classmethod
    def currency_uppercase(cls, v: str) -> str:
        return v.upper()

    @model_validator(mode="after")
    def billing_period_order(self) -> "FocusRecord":
        if self.billing_period_end <= self.billing_period_start:
            raise ValueError("billing_period_end must be after billing_period_start")
        if self.charge_period_end <= self.charge_period_start:
            raise ValueError("charge_period_end must be after charge_period_start")
        return self

    @model_validator(mode="after")
    def cost_non_negative_for_usage(self) -> "FocusRecord":
        if self.effective_cost < Decimal("0") and self.charge_category not in (
            ChargeCategory.CREDIT,
            ChargeCategory.ADJUSTMENT,
        ):
            raise ValueError(
                "effective_cost may only be negative for Credit or Adjustment charges"
            )
        return self

    @property
    def discount_amount(self) -> Decimal:
        """Savings achieved vs on-demand list price."""
        return self.list_cost - self.effective_cost

    @property
    def discount_percentage(self) -> Decimal:
        """Discount as a percentage of list price (0-100)."""
        if self.list_cost == Decimal("0"):
            return Decimal("0")
        return (self.discount_amount / self.list_cost * 100).quantize(Decimal("0.01"))

    @property
    def team(self) -> str | None:
        return self.tags.get("team") or self.tags.get("Team")

    @property
    def environment_tag(self) -> str | None:
        return (
            self.tags.get("environment")
            or self.tags.get("Environment")
            or self.tags.get("env")
        )

    model_config = {"frozen": True}


class FocusSummary(BaseModel):
    """Aggregated cost summary for a dimension slice. Used by the dashboard."""

    dimension_key: str
    dimension_value: str
    provider: CloudProvider | None = None
    period_start: datetime
    period_end: datetime
    total_effective_cost: Decimal = Field(default=Decimal("0"))
    total_list_cost: Decimal = Field(default=Decimal("0"))
    total_billed_cost: Decimal = Field(default=Decimal("0"))
    record_count: int = Field(default=0, ge=0)

    @property
    def total_discount(self) -> Decimal:
        return self.total_list_cost - self.total_effective_cost

    model_config = {"frozen": True}
