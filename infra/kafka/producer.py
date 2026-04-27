"""
CloudSense — Kafka KRaft Producer
====================================
Streams FOCUS billing records to Kafka topics using confluent-kafka.

⚡ KRaft mode — ZooKeeper-free (Kafka 3.3+ / KIP-833)
  The broker uses internal Raft consensus for metadata management.
  No external ZooKeeper cluster is required.
  See infra/kafka/kraft.properties for the broker configuration.

Topics
------
  focus.billing.raw    : raw FOCUS records (partitioned by provider_name)
  focus.billing.alerts : anomaly / cost spike alerts
  focus.actions        : approved action commands for the Action Agent
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from confluent_kafka import Producer, KafkaException
from confluent_kafka.admin import AdminClient, NewTopic

from cloudsense.sdk.focus_schema import FocusRecord

logger = logging.getLogger(__name__)

# ── Topic definitions ─────────────────────────────────────────────────────────

TOPIC_BILLING_RAW   = "focus.billing.raw"
TOPIC_BILLING_ALERT = "focus.billing.alerts"
TOPIC_ACTIONS       = "focus.actions"

_TOPICS: list[NewTopic] = [
    NewTopic(
        TOPIC_BILLING_RAW,
        num_partitions=12,          # partition by provider (3) × 4 for parallelism
        replication_factor=3,
        config={
            "retention.ms": str(7 * 24 * 3600 * 1000),   # 7 days
            "compression.type": "lz4",
            "cleanup.policy": "delete",
        },
    ),
    NewTopic(
        TOPIC_BILLING_ALERT,
        num_partitions=3,
        replication_factor=3,
        config={
            "retention.ms": str(30 * 24 * 3600 * 1000),  # 30 days
            "compression.type": "lz4",
        },
    ),
    NewTopic(
        TOPIC_ACTIONS,
        num_partitions=3,
        replication_factor=3,
        config={
            "retention.ms": str(90 * 24 * 3600 * 1000),  # 90 days (audit trail)
            "cleanup.policy": "delete",
        },
    ),
]

# Partition routing: provider → partition number
_PROVIDER_PARTITION: dict[str, int] = {
    "aws":   0,
    "azure": 4,
    "gcp":   8,
}


# ── Serialisation helpers ─────────────────────────────────────────────────────

def _default_serialiser(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serialisable")


def focus_record_to_bytes(record: FocusRecord) -> bytes:
    """Serialise a FocusRecord to compact JSON bytes for Kafka."""
    payload = record.to_clickhouse_row()
    return json.dumps(payload, default=_default_serialiser, ensure_ascii=False).encode("utf-8")


# ── Producer ─────────────────────────────────────────────────────────────────

@dataclass
class KafkaConfig:
    """
    Producer configuration.
    Pass bootstrap_servers as a comma-separated list of KRaft brokers.
    No ZooKeeper address is needed.
    """
    bootstrap_servers: str = "localhost:9092"
    # Exactly-once semantics
    enable_idempotence: bool = True
    acks: str = "all"                    # wait for all ISR replicas
    # Throughput settings
    linger_ms: int = 50                  # batch up to 50ms for larger batches
    batch_size: int = 1_048_576          # 1 MB batch
    compression_type: str = "lz4"
    # Reliability
    retries: int = 10
    delivery_timeout_ms: int = 120_000
    max_in_flight_requests_per_connection: int = 5  # safe with idempotence


class FocusBillingProducer:
    """
    Kafka producer that streams FOCUS billing records.

    Usage
    -----
    producer = FocusBillingProducer(KafkaConfig(bootstrap_servers="broker1:9092,broker2:9092"))
    producer.ensure_topics_exist()
    producer.send_batch(records)
    producer.flush()
    """

    def __init__(self, config: KafkaConfig) -> None:
        self._config = config
        self._producer = Producer(self._build_confluent_config(config))
        logger.info(
            "FocusBillingProducer initialised (KRaft brokers: %s)", config.bootstrap_servers
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    def ensure_topics_exist(self) -> None:
        """Idempotently create topics if they don't exist (requires broker admin privileges)."""
        admin = AdminClient({"bootstrap.servers": self._config.bootstrap_servers})
        result = admin.create_topics(_TOPICS)
        for topic, fut in result.items():
            try:
                fut.result()
                logger.info("Topic created: %s", topic)
            except KafkaException as exc:
                if "TOPIC_ALREADY_EXISTS" in str(exc):
                    logger.debug("Topic already exists: %s", topic)
                else:
                    logger.error("Failed to create topic %s: %s", topic, exc)
                    raise

    def send_record(
        self,
        record: FocusRecord,
        on_delivery: Callable | None = None,
    ) -> None:
        """Non-blocking send of a single FOCUS record."""
        value    = focus_record_to_bytes(record)
        key      = record.billing_account_id.encode("utf-8")
        partition = _PROVIDER_PARTITION.get(str(record.provider_name), 0)

        self._producer.produce(
            topic=TOPIC_BILLING_RAW,
            key=key,
            value=value,
            partition=partition,
            on_delivery=on_delivery or self._default_delivery_cb,
            headers={
                "provider":    str(record.provider_name).encode(),
                "account_id":  record.billing_account_id.encode(),
                "cs_version":  b"1.0",
            },
        )
        # Poll to trigger delivery callbacks; prevent buffer overflow
        self._producer.poll(0)

    def send_batch(
        self,
        records: list[FocusRecord],
        on_delivery: Callable | None = None,
    ) -> int:
        """
        Send a batch of records. Returns the number of records enqueued.
        Call flush() after to guarantee delivery.
        """
        sent = 0
        for record in records:
            try:
                self.send_record(record, on_delivery)
                sent += 1
            except BufferError:
                # Local queue full — flush and retry
                logger.warning("Kafka producer buffer full, flushing…")
                self._producer.flush(timeout=30)
                self.send_record(record, on_delivery)
                sent += 1
        logger.info("Enqueued %d FOCUS records to Kafka", sent)
        return sent

    def flush(self, timeout: float = 60.0) -> int:
        """
        Block until all outstanding messages are delivered.
        Returns the number of messages still in the queue (0 = success).
        """
        remaining = self._producer.flush(timeout=timeout)
        if remaining:
            logger.warning("%d messages NOT delivered within %.0fs", remaining, timeout)
        else:
            logger.info("All messages delivered successfully")
        return remaining

    def send_alert(self, alert: dict[str, Any]) -> None:
        """Send a cost anomaly alert to the alerts topic."""
        payload = json.dumps(
            {**alert, "ts": datetime.now(timezone.utc).isoformat()},
            default=_default_serialiser,
        ).encode("utf-8")
        self._producer.produce(
            topic=TOPIC_BILLING_ALERT,
            value=payload,
            on_delivery=self._default_delivery_cb,
        )
        self._producer.poll(0)

    def close(self) -> None:
        self.flush()

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _build_confluent_config(cfg: KafkaConfig) -> dict[str, Any]:
        return {
            "bootstrap.servers":                        cfg.bootstrap_servers,
            "enable.idempotence":                       cfg.enable_idempotence,
            "acks":                                     cfg.acks,
            "linger.ms":                                cfg.linger_ms,
            "batch.size":                               cfg.batch_size,
            "compression.type":                         cfg.compression_type,
            "retries":                                  cfg.retries,
            "delivery.timeout.ms":                      cfg.delivery_timeout_ms,
            "max.in.flight.requests.per.connection":    cfg.max_in_flight_requests_per_connection,
            # Observability
            "statistics.interval.ms":                   10_000,
        }

    @staticmethod
    def _default_delivery_cb(err: Any, msg: Any) -> None:
        if err:
            logger.error(
                "Kafka delivery error [topic=%s, partition=%d]: %s",
                msg.topic(), msg.partition(), err
            )
        else:
            logger.debug(
                "Delivered to %s [%d] @ offset %d",
                msg.topic(), msg.partition(), msg.offset()
            )
