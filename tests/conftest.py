"""
CloudSense test configuration and shared fixtures.
"""
import pytest
from decimal import Decimal
from datetime import datetime, timedelta

from cloudsense.core.models.enums import (
    ChargeCategory,
    CloudProvider,
    AgentName,
    ActionStatus,
    Environment,
    ResourceStatus,
)
from cloudsense.core.models.focus import FocusRecord, FocusSummary
from cloudsense.core.models.billing import (
    CloudResource,
    CostRecommendation,
    ActionRequest,
    CostAnomaly,
    CostForecast,
    TagViolation,
)


# ── Time helpers ─────────────────────────────────────────────────────────────

@pytest.fixture()
def now() -> datetime:
    return datetime(2025, 1, 15, 12, 0, 0)


@pytest.fixture()
def billing_start(now) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0)


@pytest.fixture()
def billing_end(billing_start) -> datetime:
    return billing_start + timedelta(days=31)


# ── FocusRecord fixture ───────────────────────────────────────────────────────

@pytest.fixture()
def base_focus_record(billing_start, billing_end, now) -> FocusRecord:
    return FocusRecord(
        provider=CloudProvider.AWS,
        billing_account_id="123456789012",
        billing_account_name="prod-account",
        resource_id="arn:aws:ec2:us-east-1:123456789012:instance/i-0abc123",
        resource_name="web-server-01",
        resource_type="EC2 Instance",
        service_name="Amazon EC2",
        service_category="Compute",
        region_id="us-east-1",
        billing_period_start=billing_start,
        billing_period_end=billing_end,
        charge_period_start=now,
        charge_period_end=now + timedelta(hours=1),
        effective_cost=Decimal("72.50"),
        list_cost=Decimal("100.00"),
        billed_cost=Decimal("72.50"),
        charge_category=ChargeCategory.USAGE,
        tags={"team": "platform", "environment": "production", "project": "web"},
    )


# ── Recommendation fixture ────────────────────────────────────────────────────

@pytest.fixture()
def base_recommendation(now) -> CostRecommendation:
    return CostRecommendation(
        agent=AgentName.AWS_COST,
        provider=CloudProvider.AWS,
        resource_id="i-0abc123",
        resource_type="EC2 Instance",
        title="Rightsize web-server-01 from m5.xlarge to m5.large",
        description="Instance CPU utilization is consistently below 15%. Rightsizing will save ~$45/month.",
        estimated_monthly_savings=Decimal("45.00"),
        confidence_score=0.87,
        effort_level="low",
        action_required="Change instance type from m5.xlarge to m5.large",
        created_at=now,
        expires_at=now + timedelta(days=30),
    )


# ── ActionRequest fixture ─────────────────────────────────────────────────────

@pytest.fixture()
def base_action_request(base_recommendation) -> ActionRequest:
    from uuid import uuid4
    return ActionRequest(
        recommendation_id=base_recommendation.id,
        provider=CloudProvider.AWS,
        environment=Environment.DEVELOPMENT,
        action_type="rightsize",
        target_resource_id="i-0abc123",
        parameters={"from_type": "m5.xlarge", "to_type": "m5.large"},
        rollback_plan={"restore_type": "m5.xlarge", "snapshot_id": "snap-0abc"},
        requested_by="aws_cost_agent",
    )
