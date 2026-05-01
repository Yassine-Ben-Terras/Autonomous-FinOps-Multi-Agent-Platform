"""FinOps FOCUS 1.0 Schema — Pydantic Models."""
from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, field_validator

class ChargeCategory(str, Enum):
    USAGE = "Usage"
    TAX = "Tax"
    CREDIT = "Credit"
    ADJUSTMENT = "Adjustment"
    RECURRING = "Recurring"
    ONE_TIME = "One-Time"
    REFUND = "Refund"

class ChargeSubcategory(str, Enum):
    ON_DEMAND = "On-Demand"
    COMMITMENT_BASED = "Commitment-Based"
    RESERVATION = "Reservation"
    SAVINGS_PLAN = "Savings Plan"
    SPOT = "Spot"
    NEGOTIATED = "Negotiated"

class PricingCategory(str, Enum):
    STANDARD = "Standard"
    COMMITMENT_DISCOUNT = "Commitment Discount"
    DYNAMIC = "Dynamic"
    NEGOTIATED = "Negotiated"

class FocusRecord(BaseModel):
    model_config = ConfigDict(json_schema_extra={"title": "FOCUS 1.0 Record"})
    billing_account_id: str = Field(...)
    billing_account_name: str | None = None
    billing_period_start: date = Field(...)
    billing_period_end: date = Field(...)
    charge_period_start: datetime = Field(...)
    charge_period_end: datetime = Field(...)
    resource_id: str | None = None
    resource_name: str | None = None
    resource_type: str | None = None
    service_name: str = Field(...)
    service_category: str | None = None
    region_id: str | None = None
    region_name: str | None = None
    availability_zone: str | None = None
    list_cost: Decimal = Field(..., ge=0)
    list_unit_price: Decimal | None = None
    effective_cost: Decimal = Field(..., ge=0)
    amortized_cost: Decimal | None = None
    net_amortized_cost: Decimal | None = None
    usage_quantity: Decimal = Field(..., ge=0)
    usage_unit: str = Field(...)
    usage_resource_id: str | None = None
    charge_category: ChargeCategory = Field(...)
    charge_subcategory: ChargeSubcategory | None = None
    pricing_category: PricingCategory | None = None
    commitment_discount_id: str | None = None
    commitment_discount_category: str | None = None
    commitment_discount_quantity: Decimal | None = None
    commitment_discount_status: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    provider: str = Field(...)
    provider_account_id: str = Field(...)
    invoice_issuer: str | None = None
    billing_currency: str = Field(default="USD")
    raw_line_item: dict[str, Any] | None = None

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        allowed = {"aws", "azure", "gcp"}
        if v.lower() not in allowed:
            raise ValueError(f"provider must be one of {allowed}")
        return v.lower()

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, v: Any) -> dict[str, str]:
        if v is None:
            return {}
        if isinstance(v, dict):
            return {str(k).lower(): str(v) for k, v in v.items()}
        raise ValueError("tags must be a dictionary")

    def to_clickhouse_row(self) -> dict[str, Any]:
        import json
        return {
            "billing_account_id": self.billing_account_id,
            "billing_period_start": self.billing_period_start.isoformat(),
            "billing_period_end": self.billing_period_end.isoformat(),
            "charge_period_start": self.charge_period_start.isoformat(),
            "charge_period_end": self.charge_period_end.isoformat(),
            "resource_id": self.resource_id or "",
            "resource_name": self.resource_name or "",
            "resource_type": self.resource_type or "",
            "service_name": self.service_name,
            "service_category": self.service_category or "",
            "region_id": self.region_id or "",
            "region_name": self.region_name or "",
            "availability_zone": self.availability_zone or "",
            "list_cost": float(self.list_cost),
            "effective_cost": float(self.effective_cost),
            "amortized_cost": float(self.amortized_cost or 0),
            "usage_quantity": float(self.usage_quantity),
            "usage_unit": self.usage_unit,
            "charge_category": self.charge_category.value,
            "charge_subcategory": self.charge_subcategory.value if self.charge_subcategory else "",
            "pricing_category": self.pricing_category.value if self.pricing_category else "",
            "commitment_discount_id": self.commitment_discount_id or "",
            "tags": json.dumps(self.tags),
            "provider": self.provider,
            "provider_account_id": self.provider_account_id,
            "billing_currency": self.billing_currency,
        }

class FocusBatch(BaseModel):
    records: list[FocusRecord] = Field(..., min_length=1)
    source: str = Field(...)
    ingested_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("records")
    @classmethod
    def validate_records(cls, v: list[FocusRecord]) -> list[FocusRecord]:
        if len(v) > 100_000:
            raise ValueError("Batch size cannot exceed 100,000 records")
        return v
