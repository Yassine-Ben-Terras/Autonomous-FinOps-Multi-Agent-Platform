"""
CloudSense — Unit tests: AWS connector + FOCUS schema
Uses moto to mock AWS Cost Explorer — no real AWS account needed.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from cloudsense.sdk.focus_schema import (
    ChargeCategory,
    CloudProvider,
    CommitmentDiscountType,
    FocusRecord,
    PricingCategory,
)


# ── FOCUS schema tests ─────────────────────────────────────────────────────────

class TestFocusRecord:
    def _make_record(self, **overrides) -> FocusRecord:
        defaults = dict(
            ProviderName=CloudProvider.AWS,
            BillingAccountId="123456789012",
            BillingAccountName="my-org",
            BillingPeriodStart="2024-01-01T00:00:00+00:00",
            BillingPeriodEnd="2024-02-01T00:00:00+00:00",
            ChargePeriodStart="2024-01-15T00:00:00+00:00",
            ChargePeriodEnd="2024-01-16T00:00:00+00:00",
            ChargeCategory=ChargeCategory.USAGE,
            ServiceName="Amazon EC2",
            BilledCost="125.50",
            EffectiveCost="100.00",
            ListCost="150.00",
        )
        defaults.update(overrides)
        return FocusRecord(**defaults)

    def test_basic_construction(self):
        record = self._make_record()
        assert record.provider_name == CloudProvider.AWS
        assert record.billing_account_id == "123456789012"
        assert record.effective_cost == Decimal("100.00")

    def test_cost_coercion_from_string(self):
        record = self._make_record(BilledCost="99.99")
        assert isinstance(record.billed_cost, Decimal)
        assert record.billed_cost == Decimal("99.99")

    def test_datetime_gets_utc(self):
        record = self._make_record(
            BillingPeriodStart="2024-01-01",
            BillingPeriodEnd="2024-02-01",
            ChargePeriodStart="2024-01-15",
            ChargePeriodEnd="2024-01-16",
        )
        assert record.billing_period_start.tzinfo is not None

    def test_invalid_period_order_raises(self):
        with pytest.raises(ValueError, match="BillingPeriodStart must be before"):
            self._make_record(
                BillingPeriodStart="2024-02-01T00:00:00+00:00",
                BillingPeriodEnd="2024-01-01T00:00:00+00:00",
            )

    def test_tags_default_empty(self):
        record = self._make_record()
        assert record.tags == {}

    def test_to_clickhouse_row(self):
        record = self._make_record()
        row = record.to_clickhouse_row()
        assert row["provider_name"]    == "aws"
        assert row["service_name"]     == "Amazon EC2"
        assert isinstance(row["effective_cost"], float)
        assert row["effective_cost"]   == 100.0
        assert isinstance(row["cs_ingested_at"], datetime)

    def test_optional_fields_none(self):
        record = self._make_record(ResourceId=None, UsageQuantity=None)
        row = record.to_clickhouse_row()
        assert row["resource_id"]    == ""
        assert row["usage_quantity"] is None


# ── AWS connector tests ────────────────────────────────────────────────────────

class TestAWSCostConnector:
    """Tests using a mocked boto3 Cost Explorer client."""

    def _make_ce_response(self, cost: str = "50.00") -> dict:
        return {
            "ResultsByTime": [{
                "TimePeriod": {"Start": "2024-01-01", "End": "2024-01-02"},
                "Total": {},
                "Groups": [{
                    "Keys": [
                        "Amazon Elastic Compute Cloud",
                        "123456789012",
                        "us-east-1",
                        "BoxUsage:t3.medium",
                    ],
                    "Metrics": {
                        "BlendedCost":   {"Amount": cost, "Unit": "USD"},
                        "UnblendedCost": {"Amount": cost, "Unit": "USD"},
                        "UsageQuantity": {"Amount": "24.0", "Unit": "Hrs"},
                    },
                }],
                "Estimated": False,
            }],
            "ResponseMetadata": {},
        }

    @patch("boto3.Session")
    def test_fetch_returns_focus_records(self, mock_session):
        from cloudsense.connectors.aws.cost_connector import AWSCostConnector

        mock_ce  = MagicMock()
        mock_ce.get_cost_and_usage.return_value = self._make_ce_response("50.00")
        mock_session.return_value.client.return_value = mock_ce

        connector = AWSCostConnector(
            billing_account_id="123456789012",
            billing_account_name="test-account",
        )
        connector._ce = mock_ce

        batches = list(connector.fetch_focus_records("2024-01-01", "2024-01-02"))
        assert len(batches) == 1
        assert len(batches[0]) == 1

        record = batches[0][0]
        assert isinstance(record, FocusRecord)
        assert record.provider_name   == CloudProvider.AWS
        assert record.service_name    == "Amazon Elastic Compute Cloud"
        assert record.billed_cost     == Decimal("50.00")
        assert record.region_id       == "us-east-1"

    @patch("boto3.Session")
    def test_zero_cost_rows_skipped(self, mock_session):
        from cloudsense.connectors.aws.cost_connector import AWSCostConnector

        mock_ce = MagicMock()
        mock_ce.get_cost_and_usage.return_value = self._make_ce_response("0.00")
        mock_session.return_value.client.return_value = mock_ce

        connector = AWSCostConnector(billing_account_id="123456789012")
        connector._ce = mock_ce

        batches = list(connector.fetch_focus_records("2024-01-01", "2024-01-02"))
        # Zero-cost rows should be filtered out
        assert batches == []

    @patch("boto3.Session")
    def test_reserved_instance_pricing_category(self, mock_session):
        from cloudsense.connectors.aws.cost_connector import AWSCostConnector

        mock_ce = MagicMock()
        response = self._make_ce_response("30.00")
        # Change usage type to Reserved
        response["ResultsByTime"][0]["Groups"][0]["Keys"][3] = "Reserved:t3.medium"
        mock_ce.get_cost_and_usage.return_value = response

        connector = AWSCostConnector(billing_account_id="123456789012")
        connector._ce = mock_ce

        batches = list(connector.fetch_focus_records("2024-01-01", "2024-01-02"))
        record = batches[0][0]
        assert record.pricing_category       == PricingCategory.COMMITMENT
        assert record.commitment_discount_type == CommitmentDiscountType.RESERVED_INSTANCE


# ── Kafka producer tests ────────────────────────────────────────────────────────

class TestFocusBillingProducer:
    def _make_record(self) -> FocusRecord:
        return FocusRecord(
            ProviderName=CloudProvider.AWS,
            BillingAccountId="123456789012",
            BillingPeriodStart="2024-01-01T00:00:00+00:00",
            BillingPeriodEnd="2024-02-01T00:00:00+00:00",
            ChargePeriodStart="2024-01-15T00:00:00+00:00",
            ChargePeriodEnd="2024-01-16T00:00:00+00:00",
            ServiceName="Amazon EC2",
            BilledCost="100.00",
            EffectiveCost="80.00",
            ListCost="120.00",
        )

    @patch("cloudsense.infra.kafka.producer.Producer")
    def test_send_batch_calls_produce(self, mock_producer_cls):
        from cloudsense.infra.kafka.producer import FocusBillingProducer, KafkaConfig

        mock_producer = MagicMock()
        mock_producer_cls.return_value = mock_producer

        producer = FocusBillingProducer(KafkaConfig(bootstrap_servers="localhost:9092"))
        records  = [self._make_record() for _ in range(3)]
        sent     = producer.send_batch(records)

        assert sent == 3
        assert mock_producer.produce.call_count == 3

    @patch("cloudsense.infra.kafka.producer.Producer")
    def test_aws_records_routed_to_partition_0(self, mock_producer_cls):
        from cloudsense.infra.kafka.producer import FocusBillingProducer, KafkaConfig, TOPIC_BILLING_RAW

        mock_producer = MagicMock()
        mock_producer_cls.return_value = mock_producer

        producer = FocusBillingProducer(KafkaConfig())
        producer.send_record(self._make_record())

        call_kwargs = mock_producer.produce.call_args[1]
        assert call_kwargs["topic"]     == TOPIC_BILLING_RAW
        assert call_kwargs["partition"] == 0   # AWS → partition 0
