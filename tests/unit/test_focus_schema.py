"""
Unit tests for FOCUS 1.0 schema models.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from sdk.focus_schema import (
    ChargeCategory,
    CloudProvider,
    FocusBillingRecord,
    FocusSchema,
    UsageUnit,
)


class TestFocusBillingRecord:
    """Test FOCUS billing record model."""

    def test_create_valid_record(self) -> None:
        record = FocusBillingRecord(
            billing_account_id="123456789",
            resource_id="i-abc123",
            provider=CloudProvider.AWS,
            service_name="Virtual Machine",
            region_id="us-east-1",
            effective_cost=Decimal("100.50"),
            list_cost=Decimal("120.00"),
            usage_quantity=Decimal("24.0"),
            usage_unit=UsageUnit.HOURS,
            usage_period_start=datetime(2024, 1, 1),
            usage_period_end=datetime(2024, 1, 2),
            charge_category=ChargeCategory.USAGE,
            tags={"team": "platform", "env": "production"},
        )
        assert record.provider == CloudProvider.AWS
        assert record.effective_cost == Decimal("100.50")
        assert record.team == "platform"
        assert record.environment == "production"

    def test_savings_calculation(self) -> None:
        record = FocusBillingRecord(
            billing_account_id="123",
            resource_id="r-1",
            provider=CloudProvider.AWS,
            service_name="Storage",
            region_id="us-west-2",
            effective_cost=Decimal("80.00"),
            list_cost=Decimal("100.00"),
            usage_quantity=Decimal("1.0"),
            usage_unit=UsageUnit.GB_MONTHS,
            usage_period_start=datetime(2024, 1, 1),
            usage_period_end=datetime(2024, 1, 2),
            charge_category=ChargeCategory.USAGE,
        )
        assert record.savings_vs_list == Decimal("20.00")
        assert record.discount_rate == Decimal("0.2")

    def test_invalid_period(self) -> None:
        with pytest.raises(ValidationError):
            FocusBillingRecord(
                billing_account_id="123",
                resource_id="r-1",
                provider=CloudProvider.AWS,
                service_name="VM",
                region_id="us-east-1",
                effective_cost=Decimal("10.00"),
                list_cost=Decimal("10.00"),
                usage_quantity=Decimal("1.0"),
                usage_unit=UsageUnit.HOURS,
                usage_period_start=datetime(2024, 1, 2),
                usage_period_end=datetime(2024, 1, 1),  # Invalid: end < start
                charge_category=ChargeCategory.USAGE,
            )

    def test_team_extraction(self) -> None:
        record = FocusBillingRecord(
            billing_account_id="123",
            resource_id="r-1",
            provider=CloudProvider.AZURE,
            service_name="VM",
            region_id="eastus",
            effective_cost=Decimal("50.00"),
            list_cost=Decimal("50.00"),
            usage_quantity=Decimal("1.0"),
            usage_unit=UsageUnit.HOURS,
            usage_period_start=datetime(2024, 1, 1),
            usage_period_end=datetime(2024, 1, 2),
            charge_category=ChargeCategory.USAGE,
            tags={"cost-center": "engineering", "environment": "staging"},
        )
        assert record.team == "engineering"

    def test_to_clickhouse_row(self) -> None:
        record = FocusBillingRecord(
            billing_account_id="123",
            resource_id="r-1",
            provider=CloudProvider.GCP,
            service_name="Compute Engine",
            region_id="us-central1",
            effective_cost=Decimal("75.00"),
            list_cost=Decimal("90.00"),
            usage_quantity=Decimal("100.0"),
            usage_unit=UsageUnit.HOURS,
            usage_period_start=datetime(2024, 1, 1, 0, 0, 0),
            usage_period_end=datetime(2024, 1, 2, 0, 0, 0),
            charge_category=ChargeCategory.USAGE,
            tags={"team": "data", "project": "analytics"},
        )
        row = FocusSchema.to_clickhouse_row(record)
        assert row["provider"] == "gcp"
        assert row["effective_cost"] == 75.0
        assert row["tags"] == [("team", "data"), ("project", "analytics")]


class TestFocusSchema:
    """Test schema utilities."""

    def test_validate_record_valid(self) -> None:
        record = {
            "billing_account_id": "123",
            "resource_id": "r-1",
            "provider": "aws",
            "service_name": "VM",
            "region_id": "us-east-1",
            "effective_cost": 10.0,
            "list_cost": 12.0,
            "usage_quantity": 1.0,
            "usage_unit": "hours",
            "usage_period_start": "2024-01-01",
            "usage_period_end": "2024-01-02",
            "charge_category": "Usage",
        }
        missing = FocusSchema.validate_record(record)
        assert missing == []

    def test_validate_record_missing_fields(self) -> None:
        record = {"provider": "aws", "service_name": "VM"}
        missing = FocusSchema.validate_record(record)
        assert "billing_account_id" in missing
        assert "resource_id" in missing
        assert "effective_cost" in missing

    def test_enum_values(self) -> None:
        assert CloudProvider.AWS.value == "aws"
        assert ChargeCategory.CREDIT.value == "Credit"
        assert UsageUnit.VCPU_HOURS.value == "vCPU-hours"
