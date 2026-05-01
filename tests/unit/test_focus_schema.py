"""Tests for FOCUS 1.0 schema models."""
import pytest
from decimal import Decimal
from datetime import date, datetime
from cloudsense.sdk.focus_schema import ChargeCategory, FocusRecord, FocusBatch

def test_focus_record_creation():
    record = FocusRecord(
        billing_account_id="123456789",
        billing_period_start=date(2024, 1, 1),
        billing_period_end=date(2024, 1, 31),
        charge_period_start=datetime(2024, 1, 1, 0, 0),
        charge_period_end=datetime(2024, 1, 31, 23, 59),
        service_name="Virtual Machine",
        list_cost=Decimal("100.00"),
        effective_cost=Decimal("80.00"),
        usage_quantity=Decimal("720.0"),
        usage_unit="Hours",
        charge_category=ChargeCategory.USAGE,
        provider="aws",
        provider_account_id="123456789",
    )
    assert record.provider == "aws"
    assert record.effective_cost == Decimal("80.00")
    assert record.to_clickhouse_row()["provider"] == "aws"

def test_provider_validation():
    with pytest.raises(ValueError, match="provider must be one of"):
        FocusRecord(
            billing_account_id="1",
            billing_period_start=date(2024, 1, 1),
            billing_period_end=date(2024, 1, 1),
            charge_period_start=datetime(2024, 1, 1),
            charge_period_end=datetime(2024, 1, 1),
            service_name="Test",
            list_cost=Decimal("1"),
            effective_cost=Decimal("1"),
            usage_quantity=Decimal("1"),
            usage_unit="hrs",
            charge_category=ChargeCategory.USAGE,
            provider="invalid",
            provider_account_id="1",
        )

def test_batch_size_limit():
    records = [FocusRecord(
        billing_account_id="1",
        billing_period_start=date(2024, 1, 1),
        billing_period_end=date(2024, 1, 1),
        charge_period_start=datetime(2024, 1, 1),
        charge_period_end=datetime(2024, 1, 1),
        service_name="Test",
        list_cost=Decimal("1"),
        effective_cost=Decimal("1"),
        usage_quantity=Decimal("1"),
        usage_unit="hrs",
        charge_category=ChargeCategory.USAGE,
        provider="aws",
        provider_account_id="1",
    ) for _ in range(100_001)]
    with pytest.raises(ValueError, match="Batch size cannot exceed"):
        FocusBatch(records=records, source="test")
