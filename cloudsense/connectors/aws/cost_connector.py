"""AWS Cost Explorer → FOCUS 1.0 Connector."""
from __future__ import annotations
import asyncio
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import AsyncIterator
import boto3
import structlog
from botocore.exceptions import ClientError
from cloudsense.connectors.base import CostConnector
from cloudsense.sdk.focus_schema import ChargeCategory, FocusBatch, FocusRecord

logger = structlog.get_logger()
AWS_SERVICE_MAP = {
    "Amazon Elastic Compute Cloud - Compute": "Virtual Machine",
    "Amazon Simple Storage Service": "Object Storage",
    "Amazon Relational Database Service": "Relational Database",
    "AmazonCloudWatch": "Monitoring",
    "AWS Lambda": "Serverless Function",
    "Amazon EC2 Container Service": "Container Orchestration",
    "Amazon Elastic Kubernetes Service": "Container Orchestration",
    "Amazon CloudFront": "CDN",
    "Amazon Route 53": "DNS",
    "AWS Key Management Service": "Key Management",
}
AWS_REGION_MAP = {
    "us-east-1": "US East (N. Virginia)",
    "us-west-2": "US West (Oregon)",
    "eu-west-1": "EU (Ireland)",
    "ap-southeast-1": "Asia Pacific (Singapore)",
}

class AWSCostConnector(CostConnector):
    provider = "aws"
    def __init__(self, connector_id: str, config: dict | None = None) -> None:
        super().__init__(connector_id, config)
        self._session = boto3.Session(
            aws_access_key_id=self.config.get("aws_access_key_id"),
            aws_secret_access_key=self.config.get("aws_secret_access_key"),
            region_name=self.config.get("region", "us-east-1"),
        )
        self._ce_client = self._session.client("ce")
        self._account_id = self.config.get("account_id", "unknown")

    async def health_check(self) -> dict:
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: self._ce_client.get_cost_and_usage(
                TimePeriod={"Start": "2024-01-01", "End": "2024-01-02"},
                Granularity="DAILY", Metrics=["BlendedCost"],
            ))
            return {"status": "healthy", "provider": self.provider, "connector_id": self.connector_id, "account_id": self._account_id}
        except ClientError as exc:
            logger.error("aws_health_check_failed", error=str(exc))
            return {"status": "unhealthy", "provider": self.provider, "connector_id": self.connector_id, "error": str(exc)}

    async def fetch_billing(self, start_date: date, end_date: date) -> AsyncIterator[FocusBatch]:
        logger.info("aws_fetch_start", connector_id=self.connector_id, start=start_date.isoformat(), end=end_date.isoformat())
        next_token = None
        batch_records = []
        batch_size = 5_000
        while True:
            kwargs = {
                "TimePeriod": {"Start": start_date.isoformat(), "End": (end_date + timedelta(days=1)).isoformat()},
                "Granularity": "DAILY",
                "Metrics": ["UnblendedCost", "UsageQuantity"],
                "GroupBy": [{"Type": "DIMENSION", "Key": "SERVICE"}, {"Type": "DIMENSION", "Key": "REGION"}],
            }
            if next_token:
                kwargs["NextPageToken"] = next_token
            loop = asyncio.get_event_loop()
            try:
                response = await loop.run_in_executor(None, lambda: self._ce_client.get_cost_and_usage(**kwargs))
            except ClientError as exc:
                logger.error("aws_ce_api_error", error=str(exc))
                raise
            for result_by_time in response.get("ResultsByTime", []):
                period_start = datetime.fromisoformat(result_by_time["TimePeriod"]["Start"]).date()
                period_end = datetime.fromisoformat(result_by_time["TimePeriod"]["End"]).date()
                for group in result_by_time.get("Groups", []):
                    keys = group["Keys"]
                    metrics = group["Metrics"]
                    service_name = AWS_SERVICE_MAP.get(keys[0], keys[0])
                    region_id = keys[1] if len(keys) > 1 else "global"
                    record = FocusRecord(
                        billing_account_id=self._account_id,
                        billing_period_start=period_start,
                        billing_period_end=period_end,
                        charge_period_start=datetime.combine(period_start, datetime.min.time()),
                        charge_period_end=datetime.combine(period_end, datetime.min.time()),
                        resource_id=None, resource_name=None, resource_type=None,
                        service_name=service_name,
                        service_category=_infer_category(service_name),
                        region_id=region_id,
                        region_name=AWS_REGION_MAP.get(region_id, region_id),
                        list_cost=Decimal(metrics["UnblendedCost"]["Amount"]),
                        effective_cost=Decimal(metrics["UnblendedCost"]["Amount"]),
                        usage_quantity=Decimal(metrics.get("UsageQuantity", {}).get("Amount", "0")),
                        usage_unit="Hrs",
                        charge_category=ChargeCategory.USAGE,
                        tags={},
                        provider="aws",
                        provider_account_id=self._account_id,
                    )
                    batch_records.append(record)
                    if len(batch_records) >= batch_size:
                        yield FocusBatch(records=batch_records, source="aws_ce")
                        batch_records = []
            next_token = response.get("NextPageToken")
            if not next_token:
                break
        if batch_records:
            yield FocusBatch(records=batch_records, source="aws_ce")
        logger.info("aws_fetch_complete", connector_id=self.connector_id)

    async def get_accounts(self) -> list[dict]:
        try:
            org_client = self._session.client("organizations")
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, lambda: org_client.list_accounts())
            return [{"id": acc["Id"], "name": acc["Name"], "email": acc["Email"], "status": acc["Status"]} for acc in response.get("Accounts", [])]
        except ClientError:
            return [{"id": self._account_id, "name": self._account_id, "email": "", "status": "ACTIVE"}]

def _infer_category(service_name: str) -> str:
    compute = {"Virtual Machine", "Serverless Function", "Container Orchestration"}
    storage = {"Object Storage", "Block Storage", "File Storage", "Backup"}
    database = {"Relational Database", "NoSQL Database", "Cache", "Data Warehouse"}
    network = {"CDN", "DNS", "Load Balancer", "VPN"}
    if service_name in compute: return "Compute"
    if service_name in storage: return "Storage"
    if service_name in database: return "Database"
    if service_name in network: return "Networking"
    return "Other"
