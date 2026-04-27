"""
CloudSense — Kafka KRaft Consumer
====================================
Consumes FOCUS billing records from the `focus.billing.raw` Kafka topic
and bulk-inserts them into ClickHouse using a micro-batch pattern.

KRaft-only: no ZooKeeper dependency.

Design:
  - Consumer group: `cloudsense-clickhouse-sink`
  - Commit strategy: manual at-least-once (safe for ClickHouse ReplacingMergeTree)
  - Batching: flush every N records OR every T seconds (whichever comes first)
  - Graceful shutdown: SIGTERM / SIGINT handled cleanly
"""

from __future__ import annotations

import json
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from typing import Any

import clickhouse_connect
from confluent_kafka import Consumer, KafkaError, KafkaException, Message

from cloudsense.infra.kafka.producer import TOPIC_BILLING_RAW

logger = logging.getLogger(__name__)


@dataclass
class ConsumerConfig:
    bootstrap_servers: str  = "localhost:9092"
    group_id: str           = "cloudsense-clickhouse-sink"
    # Micro-batch settings
    batch_size: int         = 2_000     # flush after this many records
    flush_interval_s: float = 10.0      # or after this many seconds
    # Consumer settings
    auto_offset_reset: str  = "earliest"
    session_timeout_ms: int = 45_000
    max_poll_interval_ms: int = 300_000
    fetch_max_bytes: int    = 52_428_800  # 50 MB
    max_partition_fetch_bytes: int = 10_485_760  # 10 MB


@dataclass
class _Batch:
    rows: list[dict[str, Any]] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)

    def is_full(self, max_size: int) -> bool:
        return len(self.rows) >= max_size

    def is_stale(self, interval_s: float) -> bool:
        return (time.monotonic() - self.started_at) >= interval_s

    def should_flush(self, max_size: int, interval_s: float) -> bool:
        return self.is_full(max_size) or self.is_stale(interval_s)

    def reset(self) -> None:
        self.rows.clear()
        self.started_at = time.monotonic()


# Column order must match the ClickHouse `focus.billing` DDL exactly
_CLICKHOUSE_COLUMNS = [
    "provider_name", "billing_account_id", "billing_account_name",
    "sub_account_id", "sub_account_name",
    "billing_period_start", "billing_period_end",
    "charge_period_start", "charge_period_end",
    "charge_category", "charge_frequency", "charge_description",
    "resource_id", "resource_name", "resource_type",
    "region_id", "region_name", "availability_zone",
    "service_name", "service_category", "publisher_name",
    "billed_cost", "effective_cost", "list_cost",
    "list_unit_price", "contracted_cost", "contracted_unit_price",
    "billing_currency",
    "usage_quantity", "usage_unit", "pricing_quantity", "pricing_unit",
    "pricing_category",
    "commitment_discount_id", "commitment_discount_name",
    "commitment_discount_type", "commitment_discount_status",
    "tags", "cs_ingested_at",
]


class FocusBillingConsumer:
    """
    Kafka → ClickHouse streaming consumer for FOCUS billing records.

    Usage
    -----
    consumer = FocusBillingConsumer(ConsumerConfig(...), ch_client)
    consumer.run()   # blocks; handles SIGTERM/SIGINT for graceful shutdown
    """

    def __init__(self, config: ConsumerConfig, ch_client: Any) -> None:
        self._config    = config
        self._ch        = ch_client
        self._running   = False
        self._batch     = _Batch()
        self._consumer  = self._build_consumer(config)
        self._stats     = {"consumed": 0, "inserted": 0, "errors": 0, "batches": 0}

        # Graceful shutdown on SIGTERM / SIGINT (Kubernetes sends SIGTERM)
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT,  self._handle_signal)

    # ── Public API ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Block and consume messages until a shutdown signal is received."""
        self._running = True
        self._consumer.subscribe([TOPIC_BILLING_RAW], on_assign=self._on_assign)
        logger.info(
            "Consumer started (group=%s, topic=%s)",
            self._config.group_id, TOPIC_BILLING_RAW
        )

        try:
            while self._running:
                msg = self._consumer.poll(timeout=1.0)

                if msg is None:
                    # No message — check if batch should be flushed by time
                    if self._batch.rows and self._batch.is_stale(self._config.flush_interval_s):
                        self._flush_batch()
                    continue

                if msg.error():
                    self._handle_kafka_error(msg)
                    continue

                self._process_message(msg)

                if self._batch.should_flush(self._config.batch_size, self._config.flush_interval_s):
                    self._flush_batch()
                    self._consumer.commit(asynchronous=False)

        except KafkaException as exc:
            logger.error("Kafka fatal error: %s", exc)
            raise
        finally:
            # Flush remaining rows and close cleanly
            if self._batch.rows:
                self._flush_batch()
            self._consumer.close()
            logger.info("Consumer closed. Stats: %s", self._stats)

    def stop(self) -> None:
        self._running = False

    # ── Private ────────────────────────────────────────────────────────────────

    def _process_message(self, msg: Message) -> None:
        try:
            payload: dict[str, Any] = json.loads(msg.value().decode("utf-8"))
            self._batch.rows.append(payload)
            self._stats["consumed"] += 1
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning("Failed to deserialise message offset=%d: %s", msg.offset(), exc)
            self._stats["errors"] += 1

    def _flush_batch(self) -> None:
        if not self._batch.rows:
            return

        batch_size = len(self._batch.rows)
        t0 = time.perf_counter()

        try:
            # Build list-of-lists for ClickHouse bulk insert
            data = [
                [row.get(col) for col in _CLICKHOUSE_COLUMNS]
                for row in self._batch.rows
            ]
            self._ch.insert(
                "focus.billing",
                data,
                column_names=_CLICKHOUSE_COLUMNS,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self._stats["inserted"] += batch_size
            self._stats["batches"]  += 1
            logger.info(
                "Flushed %d rows to ClickHouse in %.1fms (total inserted: %d)",
                batch_size, elapsed_ms, self._stats["inserted"]
            )
        except Exception as exc:
            logger.error("ClickHouse insert failed (%d rows): %s", batch_size, exc)
            self._stats["errors"] += batch_size
            # Don't re-raise — log and continue so the consumer doesn't crash
        finally:
            self._batch.reset()

    def _handle_kafka_error(self, msg: Message) -> None:
        err = msg.error()
        if err.code() == KafkaError._PARTITION_EOF:
            logger.debug("Reached end of partition %d", msg.partition())
        else:
            logger.error("Kafka error on partition %d: %s", msg.partition(), err)
            self._stats["errors"] += 1

    def _handle_signal(self, signum: int, _frame: Any) -> None:
        logger.info("Received signal %d — initiating graceful shutdown", signum)
        self._running = False

    @staticmethod
    def _on_assign(consumer: Consumer, partitions: list) -> None:
        logger.info(
            "Partition assignment: %s",
            [f"{p.topic}[{p.partition}]" for p in partitions]
        )

    @staticmethod
    def _build_consumer(config: ConsumerConfig) -> Consumer:
        return Consumer({
            "bootstrap.servers":          config.bootstrap_servers,
            "group.id":                   config.group_id,
            "auto.offset.reset":          config.auto_offset_reset,
            "enable.auto.commit":         False,     # manual commit only
            "session.timeout.ms":         config.session_timeout_ms,
            "max.poll.interval.ms":       config.max_poll_interval_ms,
            "fetch.max.bytes":            config.fetch_max_bytes,
            "max.partition.fetch.bytes":  config.max_partition_fetch_bytes,
            "isolation.level":            "read_committed",  # only read committed msgs
            "statistics.interval.ms":     30_000,
        })


# ── Entrypoint ────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    ch = clickhouse_connect.get_client(
        host=os.environ.get("CLICKHOUSE_HOST", "localhost"),
        port=int(os.environ.get("CLICKHOUSE_PORT", "8123")),
        database=os.environ.get("CLICKHOUSE_DB", "focus"),
        username=os.environ.get("CLICKHOUSE_USER", "cloudsense"),
        password=os.environ.get("CLICKHOUSE_PASSWORD", ""),
    )

    cfg = ConsumerConfig(
        bootstrap_servers=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
        batch_size=int(os.environ.get("CONSUMER_BATCH_SIZE", "2000")),
        flush_interval_s=float(os.environ.get("CONSUMER_FLUSH_INTERVAL_S", "10")),
    )

    consumer = FocusBillingConsumer(cfg, ch)
    consumer.run()


if __name__ == "__main__":
    main()
