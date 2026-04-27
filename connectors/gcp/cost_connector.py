"""
CloudSense — GCP Cost Connector
=================================
Reads billing data from the GCP Cloud Billing BigQuery export and
normalises it into the FOCUS 1.0 schema.

Prerequisites (GCP side):
  1. Enable Cloud Billing export to BigQuery in the GCP console.
  2. Grant the CloudSense service account roles/bigquery.dataViewer
     on the billing dataset.

GCP IAM roles required:
  - roles/bigquery.dataViewer  (on the billing export dataset)
  - roles/bigquery.jobUser     (on the project running queries)

Authentication:
  - Set GOOGLE_APPLICATION_CREDENTIALS env var to a service account key JSON, OR
  - Use Workload Identity (recommended for GKE deployments)
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterator

from google.cloud import bigquery
from google.oauth2 import service_account
from tenacity import retry, stop_after_attempt, wait_exponential

from cloudsense.sdk.focus_schema import (
    ChargeCategory,
    ChargeFrequency,
    CloudProvider,
    CommitmentDiscountType,
    FocusRecord,
    PricingCategory,
)

logger = logging.getLogger(__name__)

_GCP_SERVICE_MAP: dict[str, str] = {
    "Compute Engine": "Compute",
    "Cloud Storage": "Storage",
    "Cloud SQL": "Database",
    "Google Kubernetes Engine": "Compute",
    "Cloud Functions": "Compute",
    "Cloud Run": "Compute",
    "BigQuery": "Analytics",
    "Cloud Spanner": "Database",
    "Cloud Bigtable": "Database",
    "Cloud Pub/Sub": "Integration",
    "Vertex AI": "AI/ML",
    "Gemini": "AI/ML",
    "Cloud Networking": "Networking",
}

# BigQuery SQL for the standard GCP billing export schema
_BILLING_QUERY = """
SELECT
    project.id                                    AS project_id,
    project.name                                  AS project_name,
    service.description                           AS service_name,
    sku.description                               AS sku_description,
    location.region                               AS region_id,
    usage_start_time,
    usage_end_time,
    -- Costs
    SUM(cost)                                     AS billed_cost,
    SUM(cost)                                     AS effective_cost,
    -- Credits reduce cost — sum them separately
    SUM(IFNULL((
        SELECT SUM(c.amount)
        FROM UNNEST(credits) AS c
    ), 0))                                        AS total_credits,
    -- Usage
    SUM(usage.amount)                             AS usage_quantity,
    MAX(usage.unit)                               AS usage_unit,
    -- Commitment discounts
    MAX(invoice.month)                            AS invoice_month,
    -- Detect CUD / SUD from credits
    MAX(CASE
        WHEN EXISTS(
            SELECT 1 FROM UNNEST(credits) c
            WHERE c.type IN ('COMMITTED_USAGE_DISCOUNT', 'COMMITTED_USAGE_DISCOUNT_DOLLAR_BASE')
        ) THEN 'CUD'
        WHEN EXISTS(
            SELECT 1 FROM UNNEST(credits) c
            WHERE c.type = 'SUSTAINED_USAGE_DISCOUNT'
        ) THEN 'SUD'
        ELSE 'NONE'
    END)                                          AS discount_type,
    -- Tags (resource labels in GCP)
    TO_JSON_STRING(
        (SELECT AS STRUCT l.key, l.value FROM UNNEST(labels) AS l LIMIT 50)
    )                                             AS labels_json
FROM
    `{dataset}.gcp_billing_export_v1_{billing_account_id_clean}`
WHERE
    DATE(_PARTITIONTIME) BETWEEN @start_date AND @end_date
    AND cost_type = 'regular'
GROUP BY
    project_id, project_name, service_name, sku_description,
    region_id, usage_start_time, usage_end_time
HAVING
    billed_cost != 0
ORDER BY
    usage_start_time
"""


class GCPCostConnector:
    """
    Read-only connector for GCP Cloud Billing via BigQuery export.

    Parameters
    ----------
    billing_account_id : GCP billing account ID (e.g. "012345-ABCDEF-789012")
    bq_dataset         : full BigQuery dataset path "{project}.{dataset}"
    credentials_path   : path to service account key JSON (optional if using ADC)
    """

    def __init__(
        self,
        billing_account_id: str,
        bq_dataset: str,
        billing_account_name: str = "",
        credentials_path: str | None = None,
    ) -> None:
        self.billing_account_id    = billing_account_id
        self.billing_account_name  = billing_account_name or billing_account_id
        self.bq_dataset            = bq_dataset

        # BigQuery table names use underscores, not dashes
        self._account_id_clean = billing_account_id.replace("-", "_")

        if credentials_path:
            credentials = service_account.Credentials.from_service_account_file(
                credentials_path,
                scopes=["https://www.googleapis.com/auth/bigquery.readonly"],
            )
            self._bq = bigquery.Client(credentials=credentials)
        else:
            self._bq = bigquery.Client()  # uses ADC / Workload Identity

        logger.info(
            "GCPCostConnector initialised for billing account %s (dataset: %s)",
            billing_account_id, bq_dataset
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    def fetch_focus_records(
        self,
        start: date | str,
        end: date | str,
    ) -> Iterator[list[FocusRecord]]:
        start_date = date.fromisoformat(str(start))
        end_date   = date.fromisoformat(str(end))

        logger.info("Fetching GCP cost data %s → %s", start_date, end_date)

        sql = _BILLING_QUERY.format(
            dataset=self.bq_dataset,
            billing_account_id_clean=self._account_id_clean,
        )

        rows = self._run_query(sql, start_date, end_date)
        records = self._parse_rows(rows, start_date, end_date)
        if records:
            yield records

    # ── Internal helpers ───────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(min=5, max=60))
    def _run_query(
        self,
        sql: str,
        start_date: date,
        end_date: date,
    ) -> list[bigquery.Row]:
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("start_date", "DATE", str(start_date)),
                bigquery.ScalarQueryParameter("end_date",   "DATE", str(end_date)),
            ]
        )
        try:
            job  = self._bq.query(sql, job_config=job_config)
            rows = list(job.result())
            logger.info("GCP BigQuery returned %d rows", len(rows))
            return rows
        except Exception as exc:
            logger.error("GCP BigQuery error: %s", exc)
            raise

    def _parse_rows(
        self,
        rows: list[Any],
        default_start: date,
        default_end: date,
    ) -> list[FocusRecord]:
        records: list[FocusRecord] = []

        billing_start = datetime(
            default_start.year, default_start.month, 1, tzinfo=timezone.utc
        )
        next_month  = (billing_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        billing_end = next_month

        for row in rows:
            billed = Decimal(str(row.billed_cost or 0))
            if billed == 0:
                continue

            credits = Decimal(str(row.total_credits or 0))
            effective = billed + credits   # credits are negative in GCP

            period_start = (
                row.usage_start_time.replace(tzinfo=timezone.utc)
                if row.usage_start_time else billing_start
            )
            period_end = (
                row.usage_end_time.replace(tzinfo=timezone.utc)
                if row.usage_end_time else next_month
            )

            service_name = row.service_name or "Unknown"
            discount_type = self._infer_commitment_type(row.discount_type or "NONE")
            pricing_cat   = (
                PricingCategory.COMMITMENT
                if discount_type == CommitmentDiscountType.CUD
                else PricingCategory.ON_DEMAND
            )

            record = FocusRecord(
                ProviderName=CloudProvider.GCP,
                BillingAccountId=self.billing_account_id,
                BillingAccountName=self.billing_account_name,
                SubAccountId=row.project_id or "",
                SubAccountName=row.project_name or row.project_id or "",
                BillingPeriodStart=billing_start,
                BillingPeriodEnd=billing_end,
                ChargePeriodStart=period_start,
                ChargePeriodEnd=period_end,
                ChargeCategory=ChargeCategory.USAGE,
                ChargeFrequency=ChargeFrequency.USAGE_BASED,
                ChargeDescription=f"{service_name} · {row.sku_description or ''}",
                RegionId=row.region_id or "",
                RegionName=row.region_id or "",
                ServiceName=service_name,
                ServiceCategory=_GCP_SERVICE_MAP.get(service_name, "Other"),
                PublisherName="Google Cloud",
                BilledCost=billed,
                EffectiveCost=effective,
                ListCost=billed,
                BillingCurrency="USD",
                UsageQuantity=Decimal(str(row.usage_quantity)) if row.usage_quantity else None,
                UsageUnit=row.usage_unit or None,
                PricingCategory=pricing_cat,
                CommitmentDiscountType=discount_type,
                Tags=self._parse_labels(row.labels_json),
            )
            records.append(record)

        return records

    @staticmethod
    def _infer_commitment_type(discount_type: str) -> CommitmentDiscountType:
        if "CUD" in discount_type:
            return CommitmentDiscountType.CUD
        return CommitmentDiscountType.NONE

    @staticmethod
    def _parse_labels(labels_json: str | None) -> dict[str, str]:
        """Parse GCP resource labels JSON into a flat dict."""
        if not labels_json:
            return {}
        import json
        try:
            data = json.loads(labels_json)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
            if isinstance(data, list):
                return {str(item.get("key", "")): str(item.get("value", "")) for item in data}
        except (json.JSONDecodeError, TypeError):
            pass
        return {}
