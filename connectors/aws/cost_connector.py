"""
CloudSense — AWS Cost Connector
================================
Fetches billing data from AWS Cost Explorer and normalises it
into the FOCUS 1.0 schema.

IAM permissions required (read-only):
  - ce:GetCostAndUsage
  - ce:GetCostAndUsageWithResources
  - ce:GetDimensionValues
  - ce:GetReservationUtilization
  - ce:GetSavingsPlanUtilization
  - ce:GetAnomalies
  - organizations:ListAccounts  (optional, for multi-account)
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterator

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
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

# AWS service name → FOCUS ServiceCategory mapping
_SERVICE_CATEGORY_MAP: dict[str, str] = {
    "Amazon Elastic Compute Cloud": "Compute",
    "Amazon Relational Database Service": "Database",
    "Amazon Simple Storage Service": "Storage",
    "Amazon CloudFront": "Networking",
    "AWS Lambda": "Compute",
    "Amazon Elastic Kubernetes Service": "Compute",
    "Amazon DynamoDB": "Database",
    "Amazon ElastiCache": "Database",
    "Amazon Virtual Private Cloud": "Networking",
    "Amazon Route 53": "Networking",
    "AWS Key Management Service": "Security",
    "Amazon SageMaker": "AI/ML",
    "Amazon Bedrock": "AI/ML",
}

# Map AWS usage type prefixes to FOCUS PricingCategory
_PRICING_CAT_MAP: dict[str, PricingCategory] = {
    "Reserved": PricingCategory.COMMITMENT,
    "SavingsPlan": PricingCategory.COMMITMENT,
    "Spot": PricingCategory.SPOT,
}


class AWSCostConnector:
    """
    Read-only connector for AWS Cost Explorer.

    Usage
    -----
    connector = AWSCostConnector(
        aws_access_key_id="...",
        aws_secret_access_key="...",
        role_arn="arn:aws:iam::123456789:role/CloudSenseReadOnly",  # optional cross-account
    )
    for batch in connector.fetch_focus_records(start="2024-01-01", end="2024-02-01"):
        ingest(batch)
    """

    # CE returns max 8760 rows per call — we page automatically
    _MAX_RESULTS_PER_PAGE = 1000

    def __init__(
        self,
        billing_account_id: str,
        billing_account_name: str = "",
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        aws_session_token: str | None = None,
        role_arn: str | None = None,
        region_name: str = "us-east-1",
    ) -> None:
        self.billing_account_id   = billing_account_id
        self.billing_account_name = billing_account_name or billing_account_id
        self._role_arn = role_arn

        boto_cfg = Config(
            region_name=region_name,
            retries={"max_attempts": 5, "mode": "adaptive"},
        )

        session_kwargs: dict[str, Any] = {}
        if aws_access_key_id:
            session_kwargs["aws_access_key_id"]     = aws_access_key_id
            session_kwargs["aws_secret_access_key"] = aws_secret_access_key
            session_kwargs["aws_session_token"]     = aws_session_token

        session = boto3.Session(**session_kwargs)

        if role_arn:
            session = self._assume_role(session, role_arn, region_name)

        self._ce = session.client("ce", config=boto_cfg)
        logger.info("AWSCostConnector initialised for account %s", billing_account_id)

    # ── Public API ─────────────────────────────────────────────────────────────

    def fetch_focus_records(
        self,
        start: date | str,
        end: date | str,
        granularity: str = "DAILY",      # DAILY | MONTHLY
        include_resources: bool = False,  # set True for resource-level breakdown (slower)
    ) -> Iterator[list[FocusRecord]]:
        """
        Yield batches of FocusRecord objects covering the requested period.

        Parameters
        ----------
        start : first day (inclusive)
        end   : last day (exclusive, matching AWS CE semantics)
        """
        start_str = str(start) if isinstance(start, date) else start
        end_str   = str(end)   if isinstance(end,   date) else end

        logger.info(
            "Fetching AWS cost data %s → %s (granularity=%s)", start_str, end_str, granularity
        )

        group_by = [
            {"Type": "DIMENSION", "Key": "SERVICE"},
            {"Type": "DIMENSION", "Key": "LINKED_ACCOUNT"},
            {"Type": "DIMENSION", "Key": "REGION"},
            {"Type": "DIMENSION", "Key": "USAGE_TYPE"},
        ]

        next_token: str | None = None

        while True:
            response = self._get_cost_and_usage(
                start_str, end_str, granularity, group_by, next_token
            )
            records = self._parse_response(response, granularity)
            if records:
                yield records

            next_token = response.get("NextPageToken")
            if not next_token:
                break

    # ── Internal helpers ───────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(min=2, max=30))
    def _get_cost_and_usage(
        self,
        start: str,
        end: str,
        granularity: str,
        group_by: list[dict[str, str]],
        next_token: str | None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "TimePeriod": {"Start": start, "End": end},
            "Granularity": granularity,
            "Metrics": ["BlendedCost", "UnblendedCost", "UsageQuantity", "NormalizedUsageAmount"],
            "GroupBy": group_by,
        }
        if next_token:
            kwargs["NextPageToken"] = next_token

        try:
            return self._ce.get_cost_and_usage(**kwargs)
        except ClientError as exc:
            logger.error("AWS CE API error: %s", exc)
            raise

    def _parse_response(
        self, response: dict[str, Any], granularity: str
    ) -> list[FocusRecord]:
        records: list[FocusRecord] = []

        for time_period_result in response.get("ResultsByTime", []):
            period_start = datetime.fromisoformat(
                time_period_result["TimePeriod"]["Start"]
            ).replace(tzinfo=timezone.utc)
            period_end = datetime.fromisoformat(
                time_period_result["TimePeriod"]["End"]
            ).replace(tzinfo=timezone.utc)

            # Billing period = full month containing charge_period_start
            billing_start = period_start.replace(day=1)
            next_month = (billing_start.replace(day=28) + timedelta(days=4)).replace(day=1)
            billing_end = next_month

            for group in time_period_result.get("Groups", []):
                keys = group.get("Keys", [])
                service_name  = keys[0] if len(keys) > 0 else "Unknown"
                linked_acct   = keys[1] if len(keys) > 1 else self.billing_account_id
                region_id     = keys[2] if len(keys) > 2 else ""
                usage_type    = keys[3] if len(keys) > 3 else ""

                metrics  = group.get("Metrics", {})
                blended  = Decimal(metrics.get("BlendedCost",   {}).get("Amount", "0"))
                unblend  = Decimal(metrics.get("UnblendedCost", {}).get("Amount", "0"))
                usage_q  = metrics.get("UsageQuantity", {}).get("Amount")
                usage_u  = metrics.get("UsageQuantity", {}).get("Unit", "")

                # Skip zero-cost rows
                if blended == 0 and unblend == 0:
                    continue

                pricing_cat  = self._infer_pricing_category(usage_type)
                commit_type  = self._infer_commitment_type(usage_type)

                record = FocusRecord(
                    ProviderName=CloudProvider.AWS,
                    BillingAccountId=self.billing_account_id,
                    BillingAccountName=self.billing_account_name,
                    SubAccountId=linked_acct,
                    SubAccountName=linked_acct,
                    BillingPeriodStart=billing_start,
                    BillingPeriodEnd=billing_end,
                    ChargePeriodStart=period_start,
                    ChargePeriodEnd=period_end,
                    ChargeCategory=ChargeCategory.USAGE,
                    ChargeFrequency=ChargeFrequency.USAGE_BASED,
                    ChargeDescription=f"{service_name} · {usage_type}",
                    RegionId=region_id,
                    RegionName=region_id,
                    ServiceName=service_name,
                    ServiceCategory=_SERVICE_CATEGORY_MAP.get(service_name, "Other"),
                    PublisherName="Amazon Web Services",
                    BilledCost=blended,
                    EffectiveCost=unblend,
                    ListCost=unblend,          # CE doesn't expose list cost directly
                    BillingCurrency="USD",
                    UsageQuantity=Decimal(usage_q) if usage_q else None,
                    UsageUnit=usage_u or None,
                    PricingCategory=pricing_cat,
                    CommitmentDiscountType=commit_type,
                    Tags={},                   # Tag enrichment done by Tagging Agent
                )
                records.append(record)

        return records

    @staticmethod
    def _infer_pricing_category(usage_type: str) -> PricingCategory:
        for prefix, cat in _PRICING_CAT_MAP.items():
            if prefix.lower() in usage_type.lower():
                return cat
        return PricingCategory.ON_DEMAND

    @staticmethod
    def _infer_commitment_type(usage_type: str) -> CommitmentDiscountType:
        ut = usage_type.lower()
        if "reserved" in ut:
            return CommitmentDiscountType.RESERVED_INSTANCE
        if "savingsplan" in ut:
            return CommitmentDiscountType.SAVINGS_PLAN
        return CommitmentDiscountType.NONE

    @staticmethod
    def _assume_role(
        session: boto3.Session, role_arn: str, region: str
    ) -> boto3.Session:
        """Assume a cross-account IAM role and return a new session."""
        sts = session.client("sts", region_name=region)
        creds = sts.assume_role(
            RoleArn=role_arn,
            RoleSessionName="CloudSenseReadOnly",
            DurationSeconds=3600,
        )["Credentials"]
        return boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
        )
