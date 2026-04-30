"""Pytest configuration and shared fixtures — Phases 1-4."""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

# ── Basic fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def now() -> datetime:
    return datetime.utcnow()


@pytest.fixture
def mock_clickhouse():
    """Mock ClickHouse client for unit tests — returns empty results by default."""
    client = MagicMock()
    client._client = MagicMock()
    client._client.execute = AsyncMock(return_value=([], []))
    return client


@pytest.fixture
def sample_focus_record():
    from datetime import date
    from cloudsense.sdk.focus_schema import ChargeCategory, FocusRecord
    return FocusRecord(
        billing_account_id="123456789",
        billing_period_start=date(2024, 1, 1),
        billing_period_end=date(2024, 1, 31),
        charge_period_start=datetime(2024, 1, 1),
        charge_period_end=datetime(2024, 1, 31),
        service_name="Virtual Machine",
        list_cost=Decimal("100.00"),
        effective_cost=Decimal("80.00"),
        usage_quantity=Decimal("720.0"),
        usage_unit="Hours",
        charge_category=ChargeCategory.USAGE,
        provider="aws",
        provider_account_id="123456789",
    )


# ── Billing model fixtures (used by test_billing_models.py) ──────────────────

@pytest.fixture
def base_recommendation(now):
    from cloudsense.core.models.billing import CostRecommendation
    from cloudsense.core.models.enums import AgentName, CloudProvider
    return CostRecommendation(
        agent=AgentName.AWS_COST,
        provider=CloudProvider.AWS,
        title="Stop idle EC2 instance",
        description="Instance i-0abc has <5% CPU over 14 days.",
        estimated_monthly_savings=Decimal("45.00"),
        confidence_score=0.9,
    )


@pytest.fixture
def base_action_request(base_recommendation):
    from cloudsense.core.models.billing import ActionRequest
    from cloudsense.core.models.enums import CloudProvider, Environment
    return ActionRequest(
        recommendation_id=base_recommendation.id,
        provider=CloudProvider.AWS,
        environment=Environment.DEVELOPMENT,
        action_type="rightsize",
        target_resource_id="i-0abc123",
        rollback_plan={"action": "start_instance", "instance_id": "i-0abc123"},
        requested_by="agent",
    )


# ── Agent & policy fixtures ──────────────────────────────────────────────────

@pytest.fixture
def sample_insight():
    from cloudsense.agents.shared_types import CostInsight, InsightSeverity
    return CostInsight(
        insight_id=str(uuid4()),
        agent="aws_cost_agent",
        provider="aws",
        severity=InsightSeverity.HIGH,
        title="Idle EC2 instance detected",
        description="Instance i-0abc in us-east-1 costs $120/month with <2% CPU.",
        resource_ids=["i-0abc"],
        service_name="Virtual Machine",
        region="us-east-1",
        current_monthly_cost=Decimal("120.00"),
        projected_monthly_savings=Decimal("96.00"),
        projected_annual_savings=Decimal("1152.00"),
        confidence_score=0.85,
        recommendation="Stop or right-size to t3.micro.",
        action_type="stop",
        risk_level="low",
    )


@pytest.fixture
def mock_policy_engine():
    """Policy engine that always allows actions."""
    engine = MagicMock()
    engine.evaluate = AsyncMock(return_value={"allowed": True, "reason": None})
    return engine


@pytest.fixture
def mock_policy_engine_deny():
    """Policy engine that always denies actions."""
    engine = MagicMock()
    engine.evaluate = AsyncMock(return_value={"allowed": False, "reason": "Test denial"})
    return engine


# ── Phase 4 fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def mock_action_log_repo():
    """Mock ActionLogRepository — in-memory, no DB required."""
    repo = MagicMock()
    repo.connect = AsyncMock()
    repo.close = AsyncMock()
    repo.create_approval_request = AsyncMock(return_value="test-action-id")
    repo.approve_action = AsyncMock(return_value=True)
    repo.reject_action = AsyncMock(return_value=True)
    repo.get_action = AsyncMock(return_value={
        "id": "test-action-id",
        "status": "approved",
        "provider": "aws",
        "environment": "development",
        "action_type": "stop_instance",
        "target_resource_id": "i-0test",
        "parameters": {"region": "us-east-1"},
        "rollback_plan": {},
        "requested_by": "test",
        "approved_by": "approver",
    })
    repo.list_pending_actions = AsyncMock(return_value=[])
    repo.save_rollback_plan = AsyncMock()
    repo.load_rollback_plan = AsyncMock(return_value=None)
    repo.mark_action_executed = AsyncMock()
    repo.mark_action_rolled_back = AsyncMock()
    repo.list_audit_events = AsyncMock(return_value=[])
    repo.write_audit_event = AsyncMock()
    return repo


@pytest.fixture
def mock_rollback_registry(mock_action_log_repo):
    from cloudsense.agents.specialist.action_agent import RollbackRegistry
    registry = RollbackRegistry(repo=mock_action_log_repo)
    return registry


@pytest.fixture
def sample_tag_violation():
    from cloudsense.core.models.billing import TagViolation
    from cloudsense.core.models.enums import CloudProvider
    return TagViolation(
        provider=CloudProvider.AWS,
        resource_id="i-0abc123",
        resource_type="Virtual Machine",
        billing_account_id="123456789",
        missing_tags=["team", "environment"],
        monthly_cost_at_risk=Decimal("200.00"),
    )
