"""Pytest configuration and fixtures."""
import pytest
from unittest.mock import MagicMock

@pytest.fixture
def mock_clickhouse():
    """Mock ClickHouse client for unit tests."""
    client = MagicMock()
    client._client = MagicMock()
    return client

@pytest.fixture
def sample_focus_record():
    from datetime import date, datetime
    from decimal import Decimal
    from cloudsense.sdk.focus_schema import ChargeCategory, FocusRecord
    return FocusRecord(
        billing_account_id="123",
        billing_period_start=date(2024, 1, 1),
        billing_period_end=date(2024, 1, 31),
        charge_period_start=datetime(2024, 1, 1),
        charge_period_end=datetime(2024, 1, 31),
        service_name="Virtual Machine",
        list_cost=Decimal("100"),
        effective_cost=Decimal("80"),
        usage_quantity=Decimal("720"),
        usage_unit="Hours",
        charge_category=ChargeCategory.USAGE,
        provider="aws",
        provider_account_id="123",
    )
