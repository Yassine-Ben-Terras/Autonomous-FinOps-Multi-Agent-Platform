"""
CloudSense — Azure Cost Connector
===================================
Fetches billing data from Azure Cost Management API and normalises
it into the FOCUS 1.0 schema.

Azure permissions required (Reader on the billing scope):
  - Microsoft.CostManagement/query/action
  - Microsoft.CostManagement/exports/read

Authentication options:
  a) Service Principal (client_id + client_secret + tenant_id)
  b) Managed Identity (when running inside Azure)
  c) DefaultAzureCredential (local dev — uses az login)
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterator

from azure.identity import ClientSecretCredential, DefaultAzureCredential
from azure.mgmt.costmanagement import CostManagementClient
from azure.mgmt.costmanagement.models import (
    ExportType,
    GranularityType,
    QueryAggregation,
    QueryDataset,
    QueryDefinition,
    QueryGrouping,
    QueryTimePeriod,
    TimeframeType,
)
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

_AZURE_SERVICE_MAP: dict[str, str] = {
    "Virtual Machines": "Compute",
    "Storage": "Storage",
    "SQL Database": "Database",
    "Azure Kubernetes Service": "Compute",
    "Azure Functions": "Compute",
    "Azure Cosmos DB": "Database",
    "Azure Cache for Redis": "Database",
    "Virtual Network": "Networking",
    "Azure DNS": "Networking",
    "Azure OpenAI": "AI/ML",
    "Azure Machine Learning": "AI/ML",
}


class AzureCostConnector:
    """
    Read-only connector for Azure Cost Management.

    Scope can be a subscription, resource group, or management group:
      - /subscriptions/{subscriptionId}
      - /subscriptions/{subscriptionId}/resourceGroups/{rgName}
      - /providers/Microsoft.Management/managementGroups/{mgId}
    """

    def __init__(
        self,
        subscription_id: str,
        billing_account_name: str = "",
        tenant_id: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        scope: str | None = None,
    ) -> None:
        self.subscription_id       = subscription_id
        self.billing_account_id    = subscription_id
        self.billing_account_name  = billing_account_name or subscription_id
        self.scope = scope or f"/subscriptions/{subscription_id}"

        # Credential resolution
        if tenant_id and client_id and client_secret:
            credential = ClientSecretCredential(tenant_id, client_id, client_secret)
            logger.info("Azure: using service principal authentication")
        else:
            credential = DefaultAzureCredential()
            logger.info("Azure: using DefaultAzureCredential (az login / managed identity)")

        self._client = CostManagementClient(credential)
        logger.info("AzureCostConnector initialised for subscription %s", subscription_id)

    # ── Public API ─────────────────────────────────────────────────────────────

    def fetch_focus_records(
        self,
        start: date | str,
        end: date | str,
    ) -> Iterator[list[FocusRecord]]:
        start_dt = datetime.fromisoformat(str(start)).replace(tzinfo=timezone.utc)
        end_dt   = datetime.fromisoformat(str(end)).replace(tzinfo=timezone.utc)

        logger.info("Fetching Azure cost data %s → %s", start_dt.date(), end_dt.date())

        query_def = QueryDefinition(
            type=ExportType.ACTUAL_COST,
            timeframe=TimeframeType.CUSTOM,
            time_period=QueryTimePeriod(from_property=start_dt, to=end_dt),
            dataset=QueryDataset(
                granularity=GranularityType.DAILY,
                aggregation={
                    "totalCost":  QueryAggregation(name="Cost",             function="Sum"),
                    "usageQty":   QueryAggregation(name="UsageQuantity",    function="Sum"),
                    "listCost":   QueryAggregation(name="CostUSD",          function="Sum"),
                },
                grouping=[
                    QueryGrouping(type="Dimension", name="ServiceName"),
                    QueryGrouping(type="Dimension", name="ResourceGroupName"),
                    QueryGrouping(type="Dimension", name="ResourceLocation"),
                    QueryGrouping(type="Dimension", name="MeterCategory"),
                    QueryGrouping(type="Dimension", name="PricingModel"),
                    QueryGrouping(type="Dimension", name="ReservationId"),
                ],
            ),
        )

        response = self._query_with_retry(query_def)
        records  = self._parse_response(response, start_dt, end_dt)
        if records:
            yield records

    # ── Internal helpers ───────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(min=2, max=30))
    def _query_with_retry(self, query_def: QueryDefinition) -> Any:
        try:
            return self._client.query.usage(self.scope, query_def)
        except Exception as exc:
            logger.error("Azure Cost Management API error: %s", exc)
            raise

    def _parse_response(
        self,
        response: Any,
        period_start: datetime,
        period_end: datetime,
    ) -> list[FocusRecord]:
        records: list[FocusRecord] = []

        if not response or not response.rows:
            return records

        # Build column-name → index map from response.columns
        col_idx: dict[str, int] = {
            col.name: i for i, col in enumerate(response.columns)
        }

        billing_start = period_start.replace(day=1)
        next_month    = (billing_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        billing_end   = next_month

        for row in response.rows:
            def get(col: str, default: Any = "") -> Any:
                idx = col_idx.get(col)
                return row[idx] if idx is not None else default

            cost_val     = Decimal(str(get("Cost",          0)))
            list_cost    = Decimal(str(get("CostUSD",       0)))
            usage_qty    = get("UsageQuantity", None)
            service_name = get("ServiceName",   "Unknown")
            region_id    = get("ResourceLocation", "")
            pricing_model = get("PricingModel", "OnDemand")
            reservation_id = get("ReservationId", "")
            rg_name       = get("ResourceGroupName", "")

            if cost_val == 0:
                continue

            pricing_cat  = self._infer_pricing_category(pricing_model)
            commit_type  = (
                CommitmentDiscountType.RESERVED_INSTANCE
                if reservation_id else CommitmentDiscountType.NONE
            )

            record = FocusRecord(
                ProviderName=CloudProvider.AZURE,
                BillingAccountId=self.subscription_id,
                BillingAccountName=self.billing_account_name,
                SubAccountId=rg_name,
                SubAccountName=rg_name,
                BillingPeriodStart=billing_start,
                BillingPeriodEnd=billing_end,
                ChargePeriodStart=period_start,
                ChargePeriodEnd=period_end,
                ChargeCategory=ChargeCategory.USAGE,
                ChargeFrequency=ChargeFrequency.USAGE_BASED,
                ChargeDescription=f"{service_name} · {get('MeterCategory', '')}",
                RegionId=region_id,
                RegionName=region_id,
                ServiceName=service_name,
                ServiceCategory=_AZURE_SERVICE_MAP.get(service_name, "Other"),
                PublisherName="Microsoft Azure",
                BilledCost=cost_val,
                EffectiveCost=cost_val,
                ListCost=list_cost,
                BillingCurrency="USD",
                UsageQuantity=Decimal(str(usage_qty)) if usage_qty else None,
                PricingCategory=pricing_cat,
                CommitmentDiscountId=reservation_id or None,
                CommitmentDiscountType=commit_type,
                Tags={},
            )
            records.append(record)

        return records

    @staticmethod
    def _infer_pricing_category(pricing_model: str) -> PricingCategory:
        pm = pricing_model.lower()
        if "reservation" in pm or "reserved" in pm:
            return PricingCategory.COMMITMENT
        if "spot" in pm:
            return PricingCategory.SPOT
        return PricingCategory.ON_DEMAND
