"""
CloudSense — FOCUS 1.0 Schema
==============================
Implements the FinOps Foundation FOCUS (FinOps Open Cost and Usage Specification) 1.0
data model as Pydantic v2 models.

Spec reference: https://focus.finops.org/
All field names match the FOCUS column specification exactly to guarantee
interoperability with any FOCUS-compatible tool.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any
from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Enumerations (FOCUS spec §4) ──────────────────────────────────────────────

class CloudProvider(StrEnum):
    AWS   = "aws"
    AZURE = "azure"
    GCP   = "gcp"


class ChargeCategory(StrEnum):
    """FOCUS §4.3 — high-level category of each billing line item."""
    USAGE      = "Usage"
    TAX        = "Tax"
    CREDIT     = "Credit"
    ADJUSTMENT = "Adjustment"
    PURCHASE   = "Purchase"


class ChargeFrequency(StrEnum):
    ONE_TIME  = "One-Time"
    RECURRING = "Recurring"
    USAGE_BASED = "Usage-Based"


class PricingCategory(StrEnum):
    ON_DEMAND  = "On-Demand"
    COMMITMENT = "Commitment-Based"
    SPOT       = "Dynamic"
    OTHER      = "Other"


class CommitmentDiscountType(StrEnum):
    RESERVED_INSTANCE = "Reserved"      # AWS RI / Azure RI
    SAVINGS_PLAN      = "Savings Plan"  # AWS SP
    CUD               = "Committed Use" # GCP CUD
    NONE              = "None"


# ── Core FOCUS row model ──────────────────────────────────────────────────────

class FocusRecord(BaseModel):
    """
    One billing line item in the FOCUS 1.0 schema.

    Maps to a single row in ClickHouse table `focus.billing`.
    """

    model_config = {"populate_by_name": True, "use_enum_values": True}

    # ── Mandatory FOCUS columns ───────────────────────────────────────────────

    # Identifies the cloud & account hierarchy
    provider_name: CloudProvider = Field(
        alias="ProviderName",
        description="Cloud provider that generated this cost row.",
    )
    billing_account_id: str = Field(
        alias="BillingAccountId",
        description="Normalized account/subscription/project identifier.",
    )
    billing_account_name: str = Field(
        alias="BillingAccountName",
        default="",
    )
    sub_account_id: str | None = Field(
        alias="SubAccountId",
        default=None,
        description="AWS linked account / Azure resource group / GCP project.",
    )
    sub_account_name: str | None = Field(alias="SubAccountName", default=None)

    # Time window
    billing_period_start: datetime = Field(alias="BillingPeriodStart")
    billing_period_end: datetime   = Field(alias="BillingPeriodEnd")
    charge_period_start: datetime  = Field(alias="ChargePeriodStart")
    charge_period_end: datetime    = Field(alias="ChargePeriodEnd")

    # Charge classification
    charge_category: ChargeCategory  = Field(alias="ChargeCategory", default=ChargeCategory.USAGE)
    charge_frequency: ChargeFrequency = Field(alias="ChargeFrequency", default=ChargeFrequency.USAGE_BASED)
    charge_description: str          = Field(alias="ChargeDescription", default="")

    # Resource identity
    resource_id: str | None   = Field(alias="ResourceId", default=None)
    resource_name: str | None = Field(alias="ResourceName", default=None)
    resource_type: str | None = Field(alias="ResourceType", default=None)
    region_id: str | None     = Field(alias="RegionId", default=None)
    region_name: str | None   = Field(alias="RegionName", default=None)
    availability_zone: str | None = Field(alias="AvailabilityZone", default=None)

    # Service classification
    service_name: str      = Field(alias="ServiceName", description="Normalized cross-cloud service name.")
    service_category: str  = Field(alias="ServiceCategory", default="")
    publisher_name: str    = Field(alias="PublisherName", default="")

    # Cost dimensions
    billed_cost: Decimal      = Field(alias="BilledCost", description="Actual invoice amount.")
    effective_cost: Decimal   = Field(alias="EffectiveCost", description="After discounts and amortised commitments.")
    list_cost: Decimal        = Field(alias="ListCost", description="On-demand cost with no discounts.")
    list_unit_price: Decimal  = Field(alias="ListUnitPrice", default=Decimal("0"))
    contracted_cost: Decimal  = Field(alias="ContractedCost", default=Decimal("0"))
    contracted_unit_price: Decimal = Field(alias="ContractedUnitPrice", default=Decimal("0"))
    billing_currency: str     = Field(alias="BillingCurrency", default="USD")

    # Usage dimensions
    usage_quantity: Decimal | None = Field(alias="UsageQuantity", default=None)
    usage_unit: str | None         = Field(alias="UsageUnit", default=None)
    pricing_quantity: Decimal | None = Field(alias="PricingQuantity", default=None)
    pricing_unit: str | None         = Field(alias="PricingUnit", default=None)
    pricing_category: PricingCategory = Field(alias="PricingCategory", default=PricingCategory.ON_DEMAND)

    # Commitment discounts (RI / Savings Plan / CUD)
    commitment_discount_id: str | None   = Field(alias="CommitmentDiscountId", default=None)
    commitment_discount_name: str | None = Field(alias="CommitmentDiscountName", default=None)
    commitment_discount_type: CommitmentDiscountType = Field(
        alias="CommitmentDiscountType", default=CommitmentDiscountType.NONE
    )
    commitment_discount_status: str | None = Field(alias="CommitmentDiscountStatus", default=None)

    # Tags (normalized map across all clouds)
    tags: dict[str, str] = Field(alias="Tags", default_factory=dict)

    # CloudSense-internal metadata (not part of FOCUS spec)
    _cs_ingested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    _cs_source_raw: dict[str, Any] | None = None  # original provider payload

    @field_validator("billing_period_start", "billing_period_end",
                     "charge_period_start", "charge_period_end", mode="before")
    @classmethod
    def ensure_utc(cls, v: Any) -> datetime:
        if isinstance(v, str):
            v = datetime.fromisoformat(v)
        if isinstance(v, datetime) and v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v

    @field_validator("billed_cost", "effective_cost", "list_cost", mode="before")
    @classmethod
    def coerce_decimal(cls, v: Any) -> Decimal:
        return Decimal(str(v)) if not isinstance(v, Decimal) else v

    @model_validator(mode="after")
    def validate_period_order(self) -> "FocusRecord":
        if self.billing_period_start >= self.billing_period_end:
            raise ValueError("BillingPeriodStart must be before BillingPeriodEnd")
        if self.charge_period_start >= self.charge_period_end:
            raise ValueError("ChargePeriodStart must be before ChargePeriodEnd")
        return self

    # ── Convenience helpers ───────────────────────────────────────────────────

    def to_clickhouse_row(self) -> dict[str, Any]:
        """Return a flat dict suitable for ClickHouse bulk insert."""
        return {
            "provider_name":             self.provider_name,
            "billing_account_id":        self.billing_account_id,
            "billing_account_name":      self.billing_account_name,
            "sub_account_id":            self.sub_account_id or "",
            "sub_account_name":          self.sub_account_name or "",
            "billing_period_start":      self.billing_period_start,
            "billing_period_end":        self.billing_period_end,
            "charge_period_start":       self.charge_period_start,
            "charge_period_end":         self.charge_period_end,
            "charge_category":           self.charge_category,
            "charge_frequency":          self.charge_frequency,
            "charge_description":        self.charge_description,
            "resource_id":               self.resource_id or "",
            "resource_name":             self.resource_name or "",
            "resource_type":             self.resource_type or "",
            "region_id":                 self.region_id or "",
            "region_name":               self.region_name or "",
            "availability_zone":         self.availability_zone or "",
            "service_name":              self.service_name,
            "service_category":          self.service_category,
            "publisher_name":            self.publisher_name,
            "billed_cost":               float(self.billed_cost),
            "effective_cost":            float(self.effective_cost),
            "list_cost":                 float(self.list_cost),
            "list_unit_price":           float(self.list_unit_price),
            "contracted_cost":           float(self.contracted_cost),
            "contracted_unit_price":     float(self.contracted_unit_price),
            "billing_currency":          self.billing_currency,
            "usage_quantity":            float(self.usage_quantity) if self.usage_quantity is not None else None,
            "usage_unit":                self.usage_unit or "",
            "pricing_quantity":          float(self.pricing_quantity) if self.pricing_quantity is not None else None,
            "pricing_unit":              self.pricing_unit or "",
            "pricing_category":          self.pricing_category,
            "commitment_discount_id":    self.commitment_discount_id or "",
            "commitment_discount_name":  self.commitment_discount_name or "",
            "commitment_discount_type":  self.commitment_discount_type,
            "commitment_discount_status":self.commitment_discount_status or "",
            "tags":                      self.tags,
            "cs_ingested_at":            datetime.now(timezone.utc),
        }
