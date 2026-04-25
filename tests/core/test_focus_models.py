"""
Tests for the FOCUS 1.0 core schema models.
Pure unit tests — no external services.
"""
import pytest
from decimal import Decimal
from datetime import datetime, timedelta

from pydantic import ValidationError

from cloudsense.core.models.enums import (
    ChargeCategory,
    ChargeFrequency,
    CloudProvider,
    CommitmentDiscountType,
)
from cloudsense.core.models.focus import FocusRecord, FocusSummary


class TestFocusRecordCreation:
    """FocusRecord can be instantiated with valid data."""

    def test_minimal_valid_record(self, base_focus_record):
        """A record created from the fixture is valid."""
        assert base_focus_record.provider == CloudProvider.AWS
        assert base_focus_record.billing_account_id == "123456789012"
        assert base_focus_record.service_name == "Amazon EC2"

    def test_id_is_auto_generated(self, base_focus_record):
        assert base_focus_record.id is not None

    def test_currency_is_uppercased(self, billing_start, billing_end, now):
        record = FocusRecord(
            provider=CloudProvider.GCP,
            billing_account_id="my-gcp-project",
            service_name="Compute Engine",
            billing_period_start=billing_start,
            billing_period_end=billing_end,
            charge_period_start=now,
            charge_period_end=now + timedelta(hours=1),
            effective_cost=Decimal("10.00"),
            list_cost=Decimal("10.00"),
            billed_cost=Decimal("10.00"),
            charge_category=ChargeCategory.USAGE,
            currency="usd",
        )
        assert record.currency == "USD"

    def test_tags_default_to_empty_dict(self, billing_start, billing_end, now):
        record = FocusRecord(
            provider=CloudProvider.AZURE,
            billing_account_id="sub-001",
            service_name="Virtual Machines",
            billing_period_start=billing_start,
            billing_period_end=billing_end,
            charge_period_start=now,
            charge_period_end=now + timedelta(hours=1),
            effective_cost=Decimal("5.00"),
            list_cost=Decimal("5.00"),
            billed_cost=Decimal("5.00"),
            charge_category=ChargeCategory.USAGE,
        )
        assert record.tags == {}

    def test_with_commitment_discount(self, billing_start, billing_end, now):
        record = FocusRecord(
            provider=CloudProvider.AWS,
            billing_account_id="123456789012",
            service_name="Amazon EC2",
            billing_period_start=billing_start,
            billing_period_end=billing_end,
            charge_period_start=now,
            charge_period_end=now + timedelta(hours=1),
            effective_cost=Decimal("50.00"),
            list_cost=Decimal("100.00"),
            billed_cost=Decimal("50.00"),
            charge_category=ChargeCategory.USAGE,
            commitment_discount_id="ri-0abc123",
            commitment_discount_type=CommitmentDiscountType.RESERVED_INSTANCE,
        )
        assert record.commitment_discount_type == CommitmentDiscountType.RESERVED_INSTANCE


class TestFocusRecordValidation:
    """FocusRecord rejects invalid data."""

    def test_billing_period_must_be_ordered(self, now):
        with pytest.raises(ValidationError, match="billing_period_end"):
            FocusRecord(
                provider=CloudProvider.AWS,
                billing_account_id="123456789012",
                service_name="Amazon EC2",
                billing_period_start=now,
                billing_period_end=now - timedelta(days=1),  # end before start
                charge_period_start=now,
                charge_period_end=now + timedelta(hours=1),
                effective_cost=Decimal("10.00"),
                list_cost=Decimal("10.00"),
                billed_cost=Decimal("10.00"),
                charge_category=ChargeCategory.USAGE,
            )

    def test_charge_period_must_be_ordered(self, billing_start, billing_end, now):
        with pytest.raises(ValidationError, match="charge_period_end"):
            FocusRecord(
                provider=CloudProvider.AWS,
                billing_account_id="123456789012",
                service_name="Amazon EC2",
                billing_period_start=billing_start,
                billing_period_end=billing_end,
                charge_period_start=now,
                charge_period_end=now - timedelta(hours=1),  # end before start
                effective_cost=Decimal("10.00"),
                list_cost=Decimal("10.00"),
                billed_cost=Decimal("10.00"),
                charge_category=ChargeCategory.USAGE,
            )

    def test_negative_effective_cost_allowed_for_credit(self, billing_start, billing_end, now):
        """Credits legitimately have negative cost."""
        record = FocusRecord(
            provider=CloudProvider.AWS,
            billing_account_id="123456789012",
            service_name="Amazon EC2",
            billing_period_start=billing_start,
            billing_period_end=billing_end,
            charge_period_start=now,
            charge_period_end=now + timedelta(hours=1),
            effective_cost=Decimal("-25.00"),
            list_cost=Decimal("0"),
            billed_cost=Decimal("-25.00"),
            charge_category=ChargeCategory.CREDIT,
        )
        assert record.effective_cost == Decimal("-25.00")

    def test_negative_effective_cost_rejected_for_usage(self, billing_start, billing_end, now):
        with pytest.raises(ValidationError, match="effective_cost may only be negative"):
            FocusRecord(
                provider=CloudProvider.AWS,
                billing_account_id="123456789012",
                service_name="Amazon EC2",
                billing_period_start=billing_start,
                billing_period_end=billing_end,
                charge_period_start=now,
                charge_period_end=now + timedelta(hours=1),
                effective_cost=Decimal("-10.00"),
                list_cost=Decimal("10.00"),
                billed_cost=Decimal("-10.00"),
                charge_category=ChargeCategory.USAGE,
            )

    def test_record_is_immutable(self, base_focus_record):
        with pytest.raises(Exception):
            base_focus_record.service_name = "changed"


class TestFocusRecordProperties:
    """Computed properties on FocusRecord."""

    def test_discount_amount(self, base_focus_record):
        # list=100, effective=72.50 → discount=27.50
        assert base_focus_record.discount_amount == Decimal("27.50")

    def test_discount_percentage(self, base_focus_record):
        assert base_focus_record.discount_percentage == Decimal("27.50")

    def test_discount_percentage_zero_list_cost(self, billing_start, billing_end, now):
        record = FocusRecord(
            provider=CloudProvider.AWS,
            billing_account_id="123456789012",
            service_name="Free Tier Service",
            billing_period_start=billing_start,
            billing_period_end=billing_end,
            charge_period_start=now,
            charge_period_end=now + timedelta(hours=1),
            effective_cost=Decimal("0"),
            list_cost=Decimal("0"),
            billed_cost=Decimal("0"),
            charge_category=ChargeCategory.USAGE,
        )
        assert record.discount_percentage == Decimal("0")

    def test_team_tag_accessor(self, base_focus_record):
        assert base_focus_record.team == "platform"

    def test_environment_tag_accessor(self, base_focus_record):
        assert base_focus_record.environment_tag == "production"

    def test_environment_tag_returns_none_when_missing(self, billing_start, billing_end, now):
        record = FocusRecord(
            provider=CloudProvider.AWS,
            billing_account_id="123456789012",
            service_name="Amazon EC2",
            billing_period_start=billing_start,
            billing_period_end=billing_end,
            charge_period_start=now,
            charge_period_end=now + timedelta(hours=1),
            effective_cost=Decimal("10.00"),
            list_cost=Decimal("10.00"),
            billed_cost=Decimal("10.00"),
            charge_category=ChargeCategory.USAGE,
            tags={"project": "phoenix"},
        )
        assert record.environment_tag is None


class TestFocusSummary:
    """FocusSummary aggregation model."""

    def test_basic_creation(self, billing_start, billing_end):
        summary = FocusSummary(
            dimension_key="service_name",
            dimension_value="Amazon EC2",
            provider=CloudProvider.AWS,
            period_start=billing_start,
            period_end=billing_end,
            total_effective_cost=Decimal("5000.00"),
            total_list_cost=Decimal("7000.00"),
            total_billed_cost=Decimal("5000.00"),
            record_count=150,
        )
        assert summary.total_discount == Decimal("2000.00")
        assert summary.record_count == 150

    def test_total_discount_property(self, billing_start, billing_end):
        summary = FocusSummary(
            dimension_key="region_id",
            dimension_value="us-east-1",
            period_start=billing_start,
            period_end=billing_end,
            total_effective_cost=Decimal("300.00"),
            total_list_cost=Decimal("400.00"),
            total_billed_cost=Decimal("300.00"),
        )
        assert summary.total_discount == Decimal("100.00")
