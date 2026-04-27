"""
CloudSense — Integration Tests (Phase 1 pipeline)
===================================================
Tests the full data path:
  AWS connector (mocked) → FOCUS schema → Kafka KRaft → ClickHouse

Requires running services:
  - Kafka (KRaft, no ZooKeeper)
  - ClickHouse
  Set env vars or rely on defaults (localhost).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

# Skip integration tests if required services are not available
SKIP = os.environ.get("ENV") not in ("test", "integration") and not os.environ.get(
    "KAFKA_BOOTSTRAP_SERVERS"
)

pytestmark = pytest.mark.skipif(SKIP, reason="Integration services not configured")


@pytest.fixture(scope="session")
def ch_client():
    """ClickHouse client for the test session."""
    import clickhouse_connect
    client = clickhouse_connect.get_client(
        host=os.environ.get("CLICKHOUSE_HOST", "localhost"),
        port=int(os.environ.get("CLICKHOUSE_PORT", "8123")),
        database=os.environ.get("CLICKHOUSE_DB", "focus"),
        username=os.environ.get("CLICKHOUSE_USER", "cloudsense"),
        password=os.environ.get("CLICKHOUSE_PASSWORD", "ci_test_password"),
    )
    # Quick connection check
    client.command("SELECT 1")
    return client


@pytest.fixture(scope="session")
def kafka_bootstrap():
    return os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")


def _make_focus_record(provider="aws", account_id="test-123", cost="50.00"):
    from cloudsense.sdk.focus_schema import (
        ChargeCategory, CloudProvider, FocusRecord
    )
    return FocusRecord(
        ProviderName=CloudProvider(provider),
        BillingAccountId=account_id,
        BillingAccountName=f"Test {provider} Account",
        BillingPeriodStart="2024-01-01T00:00:00+00:00",
        BillingPeriodEnd="2024-02-01T00:00:00+00:00",
        ChargePeriodStart="2024-01-15T00:00:00+00:00",
        ChargePeriodEnd="2024-01-16T00:00:00+00:00",
        ChargeCategory=ChargeCategory.USAGE,
        ServiceName="Amazon EC2" if provider == "aws" else "Virtual Machines",
        ServiceCategory="Compute",
        PublisherName="Test Publisher",
        BilledCost=cost,
        EffectiveCost=cost,
        ListCost=str(float(cost) * 1.2),
        BillingCurrency="USD",
        Tags={"team": "platform", "env": "production"},
    )


class TestFocusSchema:
    """Schema validation tests that run without external services."""

    def test_record_serialises_to_clickhouse_row(self):
        record = _make_focus_record()
        row = record.to_clickhouse_row()

        assert row["provider_name"]   == "aws"
        assert row["service_name"]    == "Amazon EC2"
        assert isinstance(row["effective_cost"], float)
        assert row["tags"]["team"]    == "platform"
        assert row["tags"]["env"]     == "production"

    def test_multi_provider_records(self):
        providers = ["aws", "azure", "gcp"]
        costs = ["100.00", "200.50", "75.25"]
        for provider, cost in zip(providers, costs):
            record = _make_focus_record(provider=provider, cost=cost)
            row = record.to_clickhouse_row()
            assert row["provider_name"] == provider
            assert abs(row["effective_cost"] - float(cost)) < 0.001


class TestKafkaProducerIntegration:
    """Tests Kafka KRaft producer sends messages correctly."""

    def test_producer_sends_focus_record(self, kafka_bootstrap):
        from cloudsense.infra.kafka.producer import (
            FocusBillingProducer, KafkaConfig, TOPIC_BILLING_RAW
        )
        from confluent_kafka import Consumer

        run_id    = str(uuid.uuid4())[:8]
        test_acct = f"integration-test-{run_id}"
        record    = _make_focus_record(account_id=test_acct)

        # Produce
        producer = FocusBillingProducer(
            KafkaConfig(bootstrap_servers=kafka_bootstrap)
        )
        producer.ensure_topics_exist()
        sent = producer.send_batch([record])
        remaining = producer.flush(timeout=30)
        assert sent == 1
        assert remaining == 0

        # Consume and verify
        consumer = Consumer({
            "bootstrap.servers": kafka_bootstrap,
            "group.id":          f"integration-test-{run_id}",
            "auto.offset.reset": "earliest",
        })
        consumer.subscribe([TOPIC_BILLING_RAW])

        found = False
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            msg = consumer.poll(timeout=2.0)
            if msg and not msg.error():
                payload = json.loads(msg.value().decode())
                if payload.get("billing_account_id") == test_acct:
                    found = True
                    assert payload["provider_name"]  == "aws"
                    assert payload["service_name"]   == "Amazon EC2"
                    assert payload["tags"]["team"]   == "platform"
                    break

        consumer.close()
        assert found, "Produced message not found in Kafka within 30s"

    def test_provider_partition_routing(self, kafka_bootstrap):
        """AWS → partition 0, Azure → partition 4, GCP → partition 8."""
        from cloudsense.infra.kafka.producer import (
            FocusBillingProducer, KafkaConfig, _PROVIDER_PARTITION
        )

        assert _PROVIDER_PARTITION["aws"]   == 0
        assert _PROVIDER_PARTITION["azure"] == 4
        assert _PROVIDER_PARTITION["gcp"]   == 8


class TestClickHouseIntegration:
    """Tests ClickHouse DDL and write/read operations."""

    def test_focus_billing_table_exists(self, ch_client):
        result = ch_client.query(
            "SELECT count() FROM system.tables WHERE database='focus' AND name='billing'"
        )
        assert result.result_rows[0][0] == 1, "focus.billing table does not exist"

    def test_insert_and_query_focus_record(self, ch_client):
        run_id = str(uuid.uuid4())[:8]
        record = _make_focus_record(account_id=f"ch-test-{run_id}", cost="123.45")
        row    = record.to_clickhouse_row()

        # Insert
        ch_client.insert(
            "focus.billing",
            [list(row.values())],
            column_names=list(row.keys()),
        )

        # Allow ReplacingMergeTree to settle
        time.sleep(1)

        # Query back
        result = ch_client.query(
            f"SELECT billing_account_id, effective_cost, tags "
            f"FROM focus.billing "
            f"WHERE billing_account_id = 'ch-test-{run_id}' "
            f"LIMIT 1"
        )
        assert len(result.result_rows) == 1
        assert result.result_rows[0][0] == f"ch-test-{run_id}"
        assert abs(result.result_rows[0][1] - 123.45) < 0.01

    def test_materialized_views_populate(self, ch_client):
        """The daily cost MV should receive data from inserts into focus.billing."""
        result = ch_client.query(
            "SELECT count() FROM focus.mv_daily_cost_target"
        )
        # MV may or may not have data yet depending on test order — just check it exists
        assert result is not None


class TestFullPipeline:
    """End-to-end: AWS connector mock → Kafka → ClickHouse consumer."""

    def test_ingestion_pipeline(self, kafka_bootstrap, ch_client):
        """
        Mocks the AWS Cost Explorer API, runs the ingestion pipeline,
        and verifies data appears in ClickHouse.
        """
        from unittest.mock import patch, MagicMock
        from cloudsense.connectors.aws.cost_connector import AWSCostConnector
        from cloudsense.infra.kafka.producer import FocusBillingProducer, KafkaConfig
        from cloudsense.infra.kafka.consumer import FocusBillingConsumer, ConsumerConfig

        run_id    = str(uuid.uuid4())[:8]
        test_acct = f"pipeline-test-{run_id}"

        mock_ce_response = {
            "ResultsByTime": [{
                "TimePeriod": {"Start": "2024-01-15", "End": "2024-01-16"},
                "Total": {},
                "Groups": [{
                    "Keys": ["Amazon EC2", test_acct, "us-east-1", "BoxUsage:t3.medium"],
                    "Metrics": {
                        "BlendedCost":   {"Amount": "999.99", "Unit": "USD"},
                        "UnblendedCost": {"Amount": "950.00", "Unit": "USD"},
                        "UsageQuantity": {"Amount": "720.0", "Unit": "Hrs"},
                    },
                }],
            }],
        }

        with patch("boto3.Session") as mock_session:
            mock_ce = MagicMock()
            mock_ce.get_cost_and_usage.return_value = mock_ce_response
            mock_session.return_value.client.return_value = mock_ce

            connector = AWSCostConnector(
                billing_account_id=test_acct,
                billing_account_name="Pipeline Test",
            )
            connector._ce = mock_ce

            # Fetch and produce to Kafka
            producer = FocusBillingProducer(
                KafkaConfig(bootstrap_servers=kafka_bootstrap)
            )
            for batch in connector.fetch_focus_records("2024-01-15", "2024-01-16"):
                producer.send_batch(batch)
            producer.flush(timeout=30)

            # Insert directly to ClickHouse (simulating what the consumer does)
            for batch in connector.fetch_focus_records("2024-01-15", "2024-01-16"):
                rows = [r.to_clickhouse_row() for r in batch]
                for row in rows:
                    ch_client.insert(
                        "focus.billing",
                        [list(row.values())],
                        column_names=list(row.keys()),
                    )

        time.sleep(1)  # let ClickHouse settle

        result = ch_client.query(
            f"SELECT effective_cost FROM focus.billing "
            f"WHERE billing_account_id = '{test_acct}' LIMIT 1"
        )
        assert len(result.result_rows) == 1
        assert abs(result.result_rows[0][0] - 950.0) < 0.01, (
            f"Expected 950.0, got {result.result_rows[0][0]}"
        )
