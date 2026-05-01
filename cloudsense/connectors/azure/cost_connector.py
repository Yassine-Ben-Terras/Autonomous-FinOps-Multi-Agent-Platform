"""Azure Cost Management → FOCUS 1.0 Connector."""
from __future__ import annotations
import asyncio
from datetime import date, datetime
from decimal import Decimal
from typing import AsyncIterator
import structlog
from azure.identity import ClientSecretCredential
from azure.mgmt.costmanagement import CostManagementClient
from azure.mgmt.costmanagement.models import QueryDefinition, QueryDataset, QueryAggregation, QueryGrouping
from cloudsense.connectors.base import CostConnector
from cloudsense.sdk.focus_schema import ChargeCategory, FocusBatch, FocusRecord

logger = structlog.get_logger()
AZURE_SERVICE_MAP = {
    "Virtual Machines": "Virtual Machine",
    "Storage": "Object Storage",
    "SQL Database": "Relational Database",
    "Azure Database for PostgreSQL": "Relational Database",
    "Azure Functions": "Serverless Function",
    "Azure Kubernetes Service": "Container Orchestration",
    "Bandwidth": "Networking",
    "Azure DNS": "DNS",
}

class AzureCostConnector(CostConnector):
    provider = "azure"
    def __init__(self, connector_id: str, config: dict | None = None) -> None:
        super().__init__(connector_id, config)
        self.subscription_id = self.config["subscription_id"]
        self.credential = ClientSecretCredential(
            tenant_id=self.config["tenant_id"],
            client_id=self.config["client_id"],
            client_secret=self.config["client_secret"],
        )
        self._client = CostManagementClient(self.credential, self.subscription_id)

    async def health_check(self) -> dict:
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: self._client.query.usage(
                scope=f"/subscriptions/{self.subscription_id}",
                parameters=QueryDefinition(type="Usage", timeframe="MonthToDate",
                    dataset=QueryDataset(aggregation={"totalCost": QueryAggregation(name="Cost", function="Sum")}, granularity="None")),
            ))
            return {"status": "healthy", "provider": self.provider, "connector_id": self.connector_id, "subscription_id": self.subscription_id}
        except Exception as exc:
            logger.error("azure_health_check_failed", error=str(exc))
            return {"status": "unhealthy", "provider": self.provider, "connector_id": self.connector_id, "error": str(exc)}

    async def fetch_billing(self, start_date: date, end_date: date) -> AsyncIterator[FocusBatch]:
        logger.info("azure_fetch_start", connector_id=self.connector_id, start=start_date.isoformat(), end=end_date.isoformat())
        query = QueryDefinition(
            type="ActualCost", timeframe="Custom",
            time_period={
                "from": datetime.combine(start_date, datetime.min.time()).isoformat(),
                "to": datetime.combine(end_date, datetime.min.time()).isoformat(),
            },
            dataset=QueryDataset(
                granularity="Daily",
                aggregation={"cost": QueryAggregation(name="Cost", function="Sum"), "usage": QueryAggregation(name="UsageQuantity", function="Sum")},
                grouping=[QueryGrouping(type="Dimension", name="ServiceName"), QueryGrouping(type="Dimension", name="ResourceLocation")],
            ),
        )
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, lambda: self._client.query.usage(scope=f"/subscriptions/{self.subscription_id}", parameters=query))
        batch_records = []
        for row in response.rows:
            usage_date = datetime.strptime(str(row[0]), "%Y%m%d").date()
            service_raw = str(row[1])
            region_raw = str(row[2])
            cost = Decimal(str(row[3]))
            usage_qty = Decimal(str(row[4]))
            service_name = AZURE_SERVICE_MAP.get(service_raw, service_raw)
            record = FocusRecord(
                billing_account_id=self.subscription_id,
                billing_period_start=usage_date, billing_period_end=usage_date,
                charge_period_start=datetime.combine(usage_date, datetime.min.time()),
                charge_period_end=datetime.combine(usage_date, datetime.min.time()),
                resource_id=None, resource_name=None, resource_type=None,
                service_name=service_name,
                service_category=_infer_azure_category(service_name),
                region_id=region_raw, region_name=region_raw,
                list_cost=cost, effective_cost=cost,
                usage_quantity=usage_qty, usage_unit="Hours",
                charge_category=ChargeCategory.USAGE,
                tags={}, provider="azure", provider_account_id=self.subscription_id,
            )
            batch_records.append(record)
            if len(batch_records) >= 5_000:
                yield FocusBatch(records=batch_records, source="azure_cm")
                batch_records = []
        if batch_records:
            yield FocusBatch(records=batch_records, source="azure_cm")
        logger.info("azure_fetch_complete", connector_id=self.connector_id)

    async def get_accounts(self) -> list[dict]:
        return [{"id": self.subscription_id, "name": self.config.get("subscription_name", self.subscription_id), "email": "", "status": "ACTIVE"}]

def _infer_azure_category(service_name: str) -> str:
    if "Virtual Machine" in service_name: return "Compute"
    if "Storage" in service_name: return "Storage"
    if "Database" in service_name: return "Database"
    return "Other"
