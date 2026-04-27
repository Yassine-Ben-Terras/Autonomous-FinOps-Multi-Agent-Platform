"""
CloudSense — Billing Ingestion Worker (Celery)
================================================
Celery tasks for pulling billing data from cloud connectors,
normalising to FOCUS, streaming to Kafka, and writing to ClickHouse.

Queue: billing_ingestion
Beat schedule: every 24h per connector (configurable)
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from celery import Celery
from celery.utils.log import get_task_logger

from services.api.config import settings

logger = get_task_logger(__name__)

# ── Celery app ─────────────────────────────────────────────────────────────────
app = Celery(
    "cloudsense_ingestion",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_routes={
        "services.ingestion.tasks.*": {"queue": "billing_ingestion"},
    },
    beat_schedule={
        # Trigger ingestion for all configured connectors every 24h
        "daily-ingestion": {
            "task": "services.ingestion.tasks.run_scheduled_ingestion",
            "schedule": 86_400,   # 24 hours in seconds
        },
    },
)


# ── Tasks ──────────────────────────────────────────────────────────────────────

@app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=300,   # 5 min backoff
    name="services.ingestion.tasks.run_ingestion_task",
)
def run_ingestion_task(
    self,
    provider: str,
    connector_id: str,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    """
    Pull billing data for one connector and persist to ClickHouse via Kafka.
    """
    logger.info(
        "Starting ingestion: provider=%s connector=%s period=%s→%s",
        provider, connector_id, start_date, end_date
    )

    start = date.fromisoformat(start_date)
    end   = date.fromisoformat(end_date)
    total_records = 0

    try:
        connector = _build_connector(provider, connector_id)
        producer  = _build_producer()
        ch_client = _build_clickhouse_client()

        for batch in connector.fetch_focus_records(start, end):
            # 1. Stream to Kafka (for real-time consumers / anomaly agent)
            producer.send_batch(batch)

            # 2. Write directly to ClickHouse (for immediate dashboard availability)
            rows = [r.to_clickhouse_row() for r in batch]
            ch_client.insert(
                "focus.billing",
                rows,
                column_names=list(rows[0].keys()) if rows else [],
            )
            total_records += len(batch)
            logger.info("Batch of %d records inserted", len(batch))

        producer.flush()

        result = {
            "status": "success",
            "provider": provider,
            "connector_id": connector_id,
            "records_ingested": total_records,
            "period": {"start": start_date, "end": end_date},
        }
        logger.info("Ingestion complete: %s", result)
        return result

    except Exception as exc:
        logger.error("Ingestion failed for %s/%s: %s", provider, connector_id, exc)
        raise self.retry(exc=exc)


@app.task(name="services.ingestion.tasks.run_scheduled_ingestion")
def run_scheduled_ingestion() -> dict[str, Any]:
    """
    Triggered by Celery beat every 24h.
    Reads all active connectors from Postgres and enqueues an ingestion task for each.
    """
    from datetime import timedelta

    end   = date.today()
    start = end - timedelta(days=1)   # yesterday's data

    # TODO: fetch active connectors from Postgres
    # For now, read from environment variables (dev mode)
    connectors = _get_configured_connectors()

    queued = 0
    for c in connectors:
        run_ingestion_task.apply_async(
            kwargs={
                "provider":     c["provider"],
                "connector_id": c["id"],
                "start_date":   str(start),
                "end_date":     str(end),
            },
            queue="billing_ingestion",
        )
        queued += 1

    return {"queued_tasks": queued, "period": {"start": str(start), "end": str(end)}}


# ── Private helpers ─────────────────────────────────────────────────────────────

def _build_connector(provider: str, connector_id: str) -> Any:
    """Build the appropriate cloud connector based on provider."""
    import os

    if provider == "aws":
        from cloudsense.connectors.aws.cost_connector import AWSCostConnector
        return AWSCostConnector(
            billing_account_id=os.environ.get("AWS_ACCOUNT_ID", connector_id),
            billing_account_name=os.environ.get("AWS_ACCOUNT_NAME", ""),
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
            role_arn=os.environ.get("AWS_ROLE_ARN"),
        )
    elif provider == "azure":
        from cloudsense.connectors.azure.cost_connector import AzureCostConnector
        return AzureCostConnector(
            subscription_id=connector_id,
            tenant_id=os.environ.get("AZURE_TENANT_ID"),
            client_id=os.environ.get("AZURE_CLIENT_ID"),
            client_secret=os.environ.get("AZURE_CLIENT_SECRET"),
        )
    elif provider == "gcp":
        from cloudsense.connectors.gcp.cost_connector import GCPCostConnector
        return GCPCostConnector(
            billing_account_id=connector_id,
            bq_dataset=os.environ.get("GCP_BQ_DATASET", ""),
            credentials_path=os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"),
        )
    else:
        raise ValueError(f"Unknown provider: {provider}")


def _build_producer() -> Any:
    from cloudsense.infra.kafka.producer import FocusBillingProducer, KafkaConfig
    return FocusBillingProducer(
        KafkaConfig(bootstrap_servers=settings.kafka_bootstrap_servers)
    )


def _build_clickhouse_client() -> Any:
    import clickhouse_connect
    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        database=settings.clickhouse_db,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
    )


def _get_configured_connectors() -> list[dict[str, str]]:
    """Dev-mode: read connector config from environment variables."""
    import os
    connectors = []
    if os.environ.get("AWS_ACCOUNT_ID"):
        connectors.append({"provider": "aws", "id": os.environ["AWS_ACCOUNT_ID"]})
    if os.environ.get("AZURE_SUBSCRIPTION_ID"):
        connectors.append({"provider": "azure", "id": os.environ["AZURE_SUBSCRIPTION_ID"]})
    if os.environ.get("GCP_BILLING_ACCOUNT_ID"):
        connectors.append({"provider": "gcp", "id": os.environ["GCP_BILLING_ACCOUNT_ID"]})
    return connectors
