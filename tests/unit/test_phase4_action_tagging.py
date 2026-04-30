"""
Unit tests for Phase 4 — Action Agent, Rollback Registry, Tagging Agent.

All tests run without real cloud credentials or DB:
  - Action Agent uses mocked executor methods
  - Rollback Registry uses the mock_action_log_repo fixture
  - Tagging Agent uses mock ClickHouse and mock LLM
"""
from __future__ import annotations

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from cloudsense.agents.specialist.action_agent import (
    ActionAgent,
    AWSActionExecutor,
    RollbackRegistry,
)
from cloudsense.agents.specialist.tagging_agent import TaggingAgent
from cloudsense.core.models.billing import ActionRequest, TagViolation
from cloudsense.core.models.enums import ActionStatus, CloudProvider, Environment


# ─────────────────────────────────────────────
# RollbackRegistry tests
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rollback_registry_register_and_get(mock_action_log_repo):
    """Registry stores and retrieves rollback plans correctly."""
    from cloudsense.agents.specialist.action_agent import RollbackRegistry
    registry = RollbackRegistry(repo=mock_action_log_repo)
    plan = {"action": "start_instance", "instance_id": "i-0abc", "region": "us-east-1"}
    await registry.register("action-001", plan, window_days=7)

    # in-memory cache hit
    cached = registry._cache.get("action-001")
    assert cached is not None
    assert cached["plan"] == plan
    assert "expires_at" in cached


@pytest.mark.asyncio
async def test_rollback_registry_mark_executed(mock_action_log_repo):
    from cloudsense.agents.specialist.action_agent import RollbackRegistry
    registry = RollbackRegistry(repo=mock_action_log_repo)
    await registry.mark_executed("action-001")
    mock_action_log_repo.mark_action_executed.assert_awaited_once_with("action-001")


@pytest.mark.asyncio
async def test_rollback_registry_mark_rolled_back(mock_action_log_repo):
    from cloudsense.agents.specialist.action_agent import RollbackRegistry
    registry = RollbackRegistry(repo=mock_action_log_repo)
    registry._cache["action-001"] = {"plan": {}, "expires_at": "", "registered_at": ""}
    await registry.mark_rolled_back("action-001")
    mock_action_log_repo.mark_action_rolled_back.assert_awaited_once_with("action-001")
    assert "action-001" not in registry._cache


# ─────────────────────────────────────────────
# ActionAgent — policy gate
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_action_agent_denied_by_policy(base_action_request, mock_rollback_registry):
    """OPA deny → action rejected before any cloud call."""
    agent = ActionAgent()
    agent._policy.evaluate = AsyncMock(return_value={"allowed": False, "reason": "Test denial"})
    agent._rollback_registry = mock_rollback_registry

    result = await agent.execute(base_action_request)

    assert result["status"] == ActionStatus.REJECTED.value
    assert "Test denial" in result["reason"]


@pytest.mark.asyncio
async def test_action_agent_production_requires_approval(mock_rollback_registry):
    """Production environment without approved_by → AWAITING_APPROVAL."""
    from cloudsense.core.models.billing import CostRecommendation
    from cloudsense.core.models.enums import AgentName

    rec = CostRecommendation(
        agent=AgentName.AWS_COST, provider=CloudProvider.AWS,
        title="T", description="D",
        estimated_monthly_savings=Decimal("100"), confidence_score=0.9,
    )
    action = ActionRequest(
        recommendation_id=rec.id,
        provider=CloudProvider.AWS,
        environment=Environment.PRODUCTION,
        action_type="stop_instance",
        target_resource_id="i-prod-001",
        rollback_plan={},
        requested_by="agent",
    )

    agent = ActionAgent()
    agent._policy.evaluate = AsyncMock(return_value={"allowed": True, "reason": None})
    agent._rollback_registry = mock_rollback_registry

    result = await agent.execute(action, approved_by=None)

    assert result["status"] == ActionStatus.AWAITING_APPROVAL.value


@pytest.mark.asyncio
async def test_action_agent_executes_stop_instance(mock_rollback_registry, base_recommendation):
    """Approved stop_instance action executes and returns COMPLETED."""
    from cloudsense.core.models.billing import ActionRequest
    stop_action = ActionRequest(
        recommendation_id=base_recommendation.id,
        provider=CloudProvider.AWS,
        environment=Environment.DEVELOPMENT,
        action_type="stop_instance",
        target_resource_id="i-0abc123",
        parameters={"region": "us-east-1"},
        rollback_plan={},
        requested_by="agent",
    )

    agent = ActionAgent()
    agent._policy.evaluate = AsyncMock(return_value={"allowed": True, "reason": None})
    agent._rollback_registry = mock_rollback_registry

    # Mock the AWS executor — no real boto3
    expected_result = {
        "action": "stop_instance", "provider": "aws",
        "resource_id": "i-0abc123", "region": "us-east-1",
        "previous_state": "running", "new_state": "stopping",
        "rollback": {"action": "start_instance", "instance_id": "i-0abc123"},
        "executed_at": "2024-01-01T00:00:00+00:00",
    }
    agent._aws.stop_instance = AsyncMock(return_value=expected_result)

    result = await agent.execute(stop_action, approved_by="approver@example.com")

    assert result["status"] == ActionStatus.COMPLETED.value
    assert "rollback_available_until" in result
    assert result["result"]["action"] == "stop_instance"


@pytest.mark.asyncio
async def test_action_agent_rollback_success(mock_rollback_registry, mock_action_log_repo):
    """Rollback retrieves plan and re-dispatches correctly."""
    from datetime import timezone
    from cloudsense.agents.specialist.action_agent import RollbackRegistry
    import datetime

    plan_entry = {
        "plan": {"provider": "aws", "rollback_action": "start_instance",
                 "instance_id": "i-0abc", "region": "us-east-1"},
        "registered_at": datetime.datetime.now(tz=timezone.utc).isoformat(),
        "expires_at": (datetime.datetime.now(tz=timezone.utc) +
                       datetime.timedelta(days=6)).isoformat(),
    }
    mock_action_log_repo.load_rollback_plan = AsyncMock(return_value=plan_entry)
    registry = RollbackRegistry(repo=mock_action_log_repo)

    agent = ActionAgent()
    agent._rollback_registry = registry
    agent._aws.start_instance = AsyncMock(return_value={
        "action": "start_instance", "provider": "aws",
        "resource_id": "i-0abc", "new_state": "running",
    })

    result = await agent.rollback("action-123")
    assert result["status"] == ActionStatus.ROLLED_BACK.value
    assert "rolled_back_at" in result


@pytest.mark.asyncio
async def test_action_agent_rollback_expired():
    """Rollback after window expiry returns error."""
    from datetime import timezone
    import datetime
    from cloudsense.agents.specialist.action_agent import RollbackRegistry

    expired_plan = {
        "plan": {"provider": "aws", "rollback_action": "start_instance", "instance_id": "i-x"},
        "registered_at": datetime.datetime.now(tz=timezone.utc).isoformat(),
        "expires_at": (datetime.datetime.now(tz=timezone.utc) -
                       datetime.timedelta(days=1)).isoformat(),
    }
    mock_repo = MagicMock()
    mock_repo.load_rollback_plan = AsyncMock(return_value=expired_plan)
    registry = RollbackRegistry(repo=mock_repo)

    agent = ActionAgent()
    agent._rollback_registry = registry

    result = await agent.rollback("old-action")
    assert "error" in result
    assert "expired" in result["error"]


@pytest.mark.asyncio
async def test_action_agent_rollback_no_plan(mock_rollback_registry, mock_action_log_repo):
    """Rollback with no plan returns error."""
    mock_action_log_repo.load_rollback_plan = AsyncMock(return_value=None)
    agent = ActionAgent()
    agent._rollback_registry = mock_rollback_registry

    result = await agent.rollback("nonexistent-action")
    assert "error" in result


@pytest.mark.asyncio
async def test_action_agent_aws_rightsize(mock_rollback_registry):
    """Right-sizing action dispatches correctly to AWSActionExecutor."""
    from cloudsense.core.models.billing import CostRecommendation
    from cloudsense.core.models.enums import AgentName

    rec = CostRecommendation(
        agent=AgentName.AWS_COST, provider=CloudProvider.AWS,
        title="T", description="D",
        estimated_monthly_savings=Decimal("50"), confidence_score=0.9,
    )
    action = ActionRequest(
        recommendation_id=rec.id,
        provider=CloudProvider.AWS,
        environment=Environment.DEVELOPMENT,
        action_type="rightsize",
        target_resource_id="i-0big",
        parameters={"region": "eu-west-1", "target_instance_type": "t3.medium",
                    "original_instance_type": "m5.xlarge"},
        rollback_plan={},
        requested_by="agent",
    )

    agent = ActionAgent()
    agent._policy.evaluate = AsyncMock(return_value={"allowed": True, "reason": None})
    agent._rollback_registry = mock_rollback_registry
    agent._aws.rightsize_instance = AsyncMock(return_value={
        "action": "rightsize_instance", "provider": "aws",
        "resource_id": "i-0big", "region": "eu-west-1",
        "original_type": "m5.xlarge", "target_type": "t3.medium",
        "rollback": {}, "executed_at": "2024-01-01T00:00:00+00:00",
    })

    result = await agent.execute(action, approved_by="auto")
    assert result["status"] == ActionStatus.COMPLETED.value
    assert result["result"]["target_type"] == "t3.medium"


# ─────────────────────────────────────────────
# ActionAgent._build_rollback_plan
# ─────────────────────────────────────────────

def test_build_rollback_plan_stop(base_action_request):
    """Rollback plan for stop_instance contains start_instance directive."""
    agent = ActionAgent()
    action = ActionRequest(
        recommendation_id=base_action_request.recommendation_id,
        provider=CloudProvider.AWS,
        environment=Environment.DEVELOPMENT,
        action_type="stop_instance",
        target_resource_id="i-0abc",
        parameters={"region": "us-west-2"},
        rollback_plan={},
        requested_by="agent",
    )
    plan = agent._build_rollback_plan(action)
    assert plan["rollback_action"] == "start_instance"
    assert plan["instance_id"] == "i-0abc"
    assert plan["region"] == "us-west-2"


def test_build_rollback_plan_rightsize(base_action_request):
    """Rollback plan for rightsize stores original_type."""
    agent = ActionAgent()
    action = ActionRequest(
        recommendation_id=base_action_request.recommendation_id,
        provider=CloudProvider.AWS,
        environment=Environment.DEVELOPMENT,
        action_type="rightsize_instance",
        target_resource_id="i-0xlarge",
        parameters={"region": "us-east-1", "original_instance_type": "m5.xlarge",
                    "target_instance_type": "t3.medium"},
        rollback_plan={},
        requested_by="agent",
    )
    plan = agent._build_rollback_plan(action)
    assert plan["original_type"] == "m5.xlarge"


def test_build_rollback_plan_azure(base_recommendation):
    agent = ActionAgent()
    action = ActionRequest(
        recommendation_id=base_recommendation.id,
        provider=CloudProvider.AZURE,
        environment=Environment.STAGING,
        action_type="stop_vm",
        target_resource_id="my-vm",
        parameters={"resource_group": "rg-prod"},
        rollback_plan={},
        requested_by="agent",
    )
    plan = agent._build_rollback_plan(action)
    assert plan["provider"] == "azure"
    assert plan["rollback_action"] == "start_vm"
    assert plan["resource_group"] == "rg-prod"


def test_build_rollback_plan_gcp(base_recommendation):
    agent = ActionAgent()
    action = ActionRequest(
        recommendation_id=base_recommendation.id,
        provider=CloudProvider.GCP,
        environment=Environment.STAGING,
        action_type="stop_instance",
        target_resource_id="my-gce-vm",
        parameters={"project_id": "my-project", "zone": "us-central1-a"},
        rollback_plan={},
        requested_by="agent",
    )
    plan = agent._build_rollback_plan(action)
    assert plan["provider"] == "gcp"
    assert plan["rollback_action"] == "start_instance"
    assert plan["project"] == "my-project"
    assert plan["zone"] == "us-central1-a"


# ─────────────────────────────────────────────
# TaggingAgent tests
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tagging_agent_scan_violations_empty_db(mock_clickhouse):
    """Scan returns empty list when ClickHouse has no data."""
    mock_clickhouse._client.execute = AsyncMock(return_value=([], []))
    agent = TaggingAgent(clickhouse_client=mock_clickhouse)
    violations = await agent.scan_violations(time_range_days=30)
    assert violations == []


@pytest.mark.asyncio
async def test_tagging_agent_scan_detects_missing_tags(mock_clickhouse):
    """Resources with empty tags dict produce TagViolation records."""
    rows = [
        ["aws", "acct-123", "i-0abc", "Virtual Machine", {}, 200.0],
    ]
    columns = [
        ["provider", "String"], ["billing_account_id", "String"],
        ["resource_id", "String"], ["resource_type", "String"],
        ["tags", "Map(String, String)"], ["monthly_cost", "Float64"],
    ]
    mock_clickhouse._client.execute = AsyncMock(return_value=(rows, columns))
    agent = TaggingAgent(clickhouse_client=mock_clickhouse)
    violations = await agent.scan_violations(time_range_days=30)
    assert len(violations) >= 1
    v = violations[0]
    assert v.resource_id == "i-0abc"
    assert "team" in v.missing_tags
    assert "environment" in v.missing_tags
    assert v.monthly_cost_at_risk == Decimal("200.0")


@pytest.mark.asyncio
async def test_tagging_agent_scan_compliant_resource(mock_clickhouse):
    """Resources with all required tags produce no violations."""
    rows = [
        ["aws", "acct-123", "i-compliant", "Virtual Machine",
         {"team": "platform", "environment": "production", "project": "api", "owner": "devops"},
         100.0],
    ]
    columns = [
        ["provider", "String"], ["billing_account_id", "String"],
        ["resource_id", "String"], ["resource_type", "String"],
        ["tags", "Map(String, String)"], ["monthly_cost", "Float64"],
    ]
    mock_clickhouse._client.execute = AsyncMock(return_value=(rows, columns))
    agent = TaggingAgent(clickhouse_client=mock_clickhouse)
    violations = await agent.scan_violations(time_range_days=30)
    assert violations == []


@pytest.mark.asyncio
async def test_tagging_agent_infer_no_api_key(mock_clickhouse):
    """Infer returns 'unknown' values when no API key configured."""
    agent = TaggingAgent(clickhouse_client=mock_clickhouse)
    agent._settings = MagicMock()
    agent._settings.anthropic_api_key = None
    inferred = await agent.infer_tags("i-0abc", "EC2 Instance", "acct-123")
    assert all(v == "unknown" for v in inferred.values())
    assert set(inferred.keys()) == {"team", "environment", "project", "owner"}


@pytest.mark.asyncio
async def test_tagging_agent_infer_with_mocked_llm(mock_clickhouse):
    """Infer calls LLM and parses JSON response correctly."""
    import json
    agent = TaggingAgent(clickhouse_client=mock_clickhouse)
    agent._settings = MagicMock()
    agent._settings.anthropic_api_key = MagicMock()
    agent._settings.anthropic_api_key.get_secret_value = lambda: "test-key"
    expected = {"team": "platform", "environment": "production",
                "project": "api-gateway", "owner": "infra-team"}
    mock_response = MagicMock()
    mock_response.content = json.dumps(expected)
    agent._llm.invoke = MagicMock(return_value=mock_response)
    inferred = await agent.infer_tags("i-0abc", "EC2 Instance", "acct-123")
    assert inferred["team"] == "platform"
    assert inferred["environment"] == "production"


@pytest.mark.asyncio
async def test_tagging_agent_analyze_returns_insights(mock_clickhouse):
    """analyze() converts violations to CostInsight list."""
    rows = [
        ["aws", "acct-123", "i-0nontag", "Virtual Machine", {}, 150.0],
    ]
    columns = [
        ["provider", "String"], ["billing_account_id", "String"],
        ["resource_id", "String"], ["resource_type", "String"],
        ["tags", "Map(String, String)"], ["monthly_cost", "Float64"],
    ]
    mock_clickhouse._client.execute = AsyncMock(return_value=(rows, columns))
    agent = TaggingAgent(clickhouse_client=mock_clickhouse)
    agent._settings = MagicMock()
    agent._settings.anthropic_api_key = None
    agent._settings.llm_default_model = "claude-sonnet-4-20250514"
    insights = await agent.analyze(time_range_days=30)
    assert len(insights) >= 1
    assert insights[0].action_type == "tag"
    assert insights[0].agent == "tagging_agent"


@pytest.mark.asyncio
async def test_tagging_agent_compliance_report(mock_clickhouse):
    """compliance_report() returns structured summary."""
    rows = [
        ["aws", "acct-123", "i-001", "VM", {}, 300.0],
        ["aws", "acct-123", "i-002", "VM", {"team": "a"}, 100.0],
    ]
    columns = [
        ["provider", "String"], ["billing_account_id", "String"],
        ["resource_id", "String"], ["resource_type", "String"],
        ["tags", "Map(String, String)"], ["monthly_cost", "Float64"],
    ]
    mock_clickhouse._client.execute = AsyncMock(return_value=(rows, columns))
    agent = TaggingAgent(clickhouse_client=mock_clickhouse)
    agent._settings = MagicMock()
    agent._settings.anthropic_api_key = None
    report = await agent.compliance_report(time_range_days=30)
    assert "total_violations" in report
    assert "total_cost_at_risk_monthly" in report
    assert "severity_breakdown" in report
    assert report["total_violations"] >= 1


# ─────────────────────────────────────────────
# TagViolation model tests (domain model)
# ─────────────────────────────────────────────

def test_tag_violation_severity_none(sample_tag_violation):
    """No missing tags → none severity."""
    from cloudsense.core.models.billing import TagViolation
    from cloudsense.core.models.enums import CloudProvider
    v = TagViolation(
        provider=CloudProvider.AWS, resource_id="i-clean",
        billing_account_id="123", missing_tags=[],
    )
    assert v.severity == "none"


def test_tag_violation_severity_high(sample_tag_violation):
    """6 missing tags → high severity."""
    from cloudsense.core.models.billing import TagViolation
    from cloudsense.core.models.enums import CloudProvider
    v = TagViolation(
        provider=CloudProvider.AWS, resource_id="i-bad",
        billing_account_id="123",
        missing_tags=["team", "env", "project", "owner", "cost-center", "app"],
    )
    assert v.severity == "high"


def test_tag_violation_monthly_cost_at_risk(sample_tag_violation):
    assert sample_tag_violation.monthly_cost_at_risk == Decimal("200.00")
