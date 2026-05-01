"""GCP BigQuery Billing Export → FOCUS 1.0 Connector."""
from __future__ import annotations
import asyncio
from datetime import date, datetime
from decimal import Decimal
from typing import AsyncIterator
import structlog
from google.cloud import bigquery
from google.cloud.bigquery import Row
from cloudsense.connectors.base import CostConnector
from cloudsense.sdk.focus_schema import ChargeCategory, FocusBatch, FocusRecord

logger = structlog.get_logger()
GCP_SERVICE_MAP = {
    "Compute Engine": "Virtual Machine",
    "Cloud Storage": "Object Storage",
    "Cloud SQL": "Relational Database",
    "Cloud Functions": "Serverless Function",
    "Google Kubernetes Engine": "Container Orchestration",
    "Cloud DNS": "DNS",
    "Cloud CDN": "CDN",
    "Cloud Load Balancing": "Load Balancer",
}

class GCPCostConnector(CostConnector):
    provider = "gcp"
    def __init__(self, connector_id: str, config: dict | None = None) -> None:
        super().__init__(connector_id, config)
        self.project_id = self.config["project_id"]
        self.dataset = self.config.get("dataset", "billing_data")
        self.table = self.config.get("table", "gcp_billing_export_v1")
        self._client = bigquery.Client(project=self.project_id)

    async def health_check(self) -> dict:
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: self._client.query(
                f"SELECT COUNT(*) FROM `{self.project_id}.{self.dataset}.{self.table}` LIMIT 1"
            ).result())
            return {"status": "healthy", "provider": self.provider, "connector_id": self.connector_id, "project_id": self.project_id}
        except Exception as exc:
            logger.error("gcp_health_check_failed", error=str(exc))
            return {"status": "unhealthy", "provider": self.provider, "connector_id": self.connector_id, "error": str(exc)}

    async def fetch_billing(self, start_date: date, end_date: date) -> AsyncIterator[FocusBatch]:
        logger.info("gcp_fetch_start", connector_id=self.connector_id, start=start_date.isoformat(), end=end_date.isoformat())
        query = f"""
        SELECT billing_account_id, project.id AS project_id, project.name AS project_name,
               service.description AS service_name, sku.description AS sku_description,
               location.location AS region_id, usage_start_time, usage_end_time, cost,
               usage.amount AS usage_amount, usage.unit AS usage_unit, credits, labels
        FROM `{self.project_id}.{self.dataset}.{self.table}`
        WHERE DATE(usage_start_time) >= @start_date AND DATE(usage_end_time) <= @end_date
        ORDER BY usage_start_time
        """
        job_config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
            bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
        ])
        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(None, lambda: list(self._client.query(query, job_config=job_config).result()))
        batch_records = []
        for row in rows:
            record = _gcp_row_to_focus(row, self.project_id)
            batch_records.append(record)
            if len(batch_records) >= 5_000:
                yield FocusBatch(records=batch_records, source="gcp_bq")
                batch_records = []
        if batch_records:
            yield FocusBatch(records=batch_records, source="gcp_bq")
        logger.info("gcp_fetch_complete", connector_id=self.connector_id, rows=len(rows))

    async def get_accounts(self) -> list[dict]:
        return [{"id": self.project_id, "name": self.config.get("project_name", self.project_id), "email": "", "status": "ACTIVE"}]

def _gcp_row_to_focus(row: Row, project_id: str) -> FocusRecord:
    service_raw = row.service_name or "Unknown"
    service_name = GCP_SERVICE_MAP.get(service_raw, service_raw)
    credits_total = sum(c["amount"] for c in (row.credits or []) if "amount" in c)
    list_cost = Decimal(str(row.cost or 0))
    effective_cost = list_cost - Decimal(str(credits_total))
    tags = {}
    if row.labels:
        for label in row.labels:
            if "key" in label and "value" in label:
                tags[label["key"]] = label["value"]
    return FocusRecord(
        billing_account_id=row.billing_account_id or project_id,
        billing_period_start=row.usage_start_time.date(),
        billing_period_end=row.usage_end_time.date(),
        charge_period_start=row.usage_start_time,
        charge_period_end=row.usage_end_time,
        resource_id=None, resource_name=row.project_name, resource_type=None,
        service_name=service_name,
        service_category=_infer_gcp_category(service_name),
        region_id=row.region_id or "global", region_name=row.region_id or "global",
        list_cost=list_cost, effective_cost=effective_cost,
        usage_quantity=Decimal(str(row.usage_amount or 0)),
        usage_unit=row.usage_unit or "seconds",
        charge_category=ChargeCategory.USAGE,
        tags=tags, provider="gcp", provider_account_id=project_id,
    )

def _infer_gcp_category(service_name: str) -> str:
    if "Virtual Machine" in service_name: return "Compute"
    if "Storage" in service_name: return "Storage"
    if "Database" in service_name: return "Database"
    if any(x in service_name for x in ["CDN", "DNS", "Load Balancer"]): return "Networking"
    return "Other"
