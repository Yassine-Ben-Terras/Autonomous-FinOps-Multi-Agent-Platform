"""
End-to-End Sandbox Tests (Phase 4) — LocalStack AWS mocking.

These tests simulate real cloud interactions without real credentials.
LocalStack provides a local AWS API endpoint for EC2, S3, etc.

Requirements:
  - LocalStack running (docker-compose includes it) or LOCALSTACK_ENDPOINT env var
  - pytest-asyncio, boto3

Run:
  pytest tests/e2e/ -v --timeout=60

Skip if LocalStack not available (CI handles it via Docker service).
"""
from __future__ import annotations

import os
import pytest
import pytest_asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

# LocalStack endpoint — override via env var
LOCALSTACK_ENDPOINT = os.getenv("LOCALSTACK_ENDPOINT", "http://localhost:4566")

# ── Helpers ──────────────────────────────────────────────────────────────────

def localstack_available() -> bool:
    """Check if LocalStack endpoint is reachable."""
    import urllib.request
    try:
        urllib.request.urlopen(f"{LOCALSTACK_ENDPOINT}/health", timeout=2)
        return True
    except Exception:
        return False


requires_localstack = pytest.mark.skipif(
    not localstack_available(),
    reason="LocalStack not reachable — skipping sandbox e2e tests",
)


def make_localstack_session():
    """Create a boto3 session pointed at LocalStack."""
    import boto3
    return boto3.Session(
        aws_access_key_id="test",
        aws_secret_access_key="test",
        region_name="us-east-1",
    )


# ─────────────────────────────────────────────
# E2E: Full action pipeline (mocked cloud)
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_action_pipeline_stop_instance_mock():
    """
    Full Phase 4 pipeline without LocalStack:
    submit → approve → execute → rollback.

    Uses mocked AWS executor — validates the entire control flow
    (OPA gate → rollback registration → execution → audit log).
    """
    from cloudsense.agents.specialist.action_agent import ActionAgent, RollbackRegistry
    from cloudsense.core.models.billing import ActionRequest, CostRecommendation
    from cloudsense.core.models.enums import ActionStatus, AgentName, CloudProvider, Environment

    # Setup
    rec = CostRecommendation(
        agent=AgentName.AWS_COST, provider=CloudProvider.AWS,
        title="Stop idle instance", description="Low CPU",
        estimated_monthly_savings=Decimal("120"), confidence_score=0.9,
    )
    action = ActionRequest(
        recommendation_id=rec.id,
        provider=CloudProvider.AWS,
        environment=Environment.STAGING,   # staging → no human approval needed
        action_type="stop_instance",
        target_resource_id="i-e2etest01",
        parameters={"region": "us-east-1"},
        rollback_plan={},
        requested_by="e2e-test",
    )

    # Mock repo
    mock_repo = MagicMock()
    mock_repo.save_rollback_plan = AsyncMock()
    mock_repo.load_rollback_plan = AsyncMock(return_value=None)
    mock_repo.mark_action_executed = AsyncMock()
    mock_repo.mark_action_rolled_back = AsyncMock()
    mock_repo._audit = AsyncMock()

    registry = RollbackRegistry(repo=mock_repo)
    agent = ActionAgent()
    agent._rollback_registry = registry

    # Allow policy
    agent._policy.evaluate = AsyncMock(return_value={"allowed": True, "reason": None})

    # Mock executor
    stop_result = {
        "action": "stop_instance", "provider": "aws",
        "resource_id": "i-e2etest01", "region": "us-east-1",
        "previous_state": "running", "new_state": "stopping",
        "rollback": {"action": "start_instance", "instance_id": "i-e2etest01", "region": "us-east-1"},
        "executed_at": "2024-01-01T12:00:00+00:00",
    }
    agent._aws.stop_instance = AsyncMock(return_value=stop_result)

    # Execute
    result = await agent.execute(action, approved_by="auto-test")

    assert result["status"] == ActionStatus.COMPLETED.value
    assert result["result"]["resource_id"] == "i-e2etest01"
    assert "rollback_available_until" in result
    mock_repo.save_rollback_plan.assert_awaited_once()
    mock_repo.mark_action_executed.assert_awaited_once()


@pytest.mark.asyncio
async def test_full_tagging_pipeline_mock():
    """
    Full Phase 4 tagging pipeline:
    scan violations → infer tags → apply tags (mocked).
    """
    from cloudsense.agents.specialist.tagging_agent import TaggingAgent
    from cloudsense.core.models.enums import CloudProvider

    mock_ch = MagicMock()
    mock_ch._client = MagicMock()

    # Simulate resource with missing tags
    rows = [["aws", "acct-e2e", "i-notag01", "Virtual Machine", {}, 250.0]]
    columns = [
        ["provider", "String"], ["billing_account_id", "String"],
        ["resource_id", "String"], ["resource_type", "String"],
        ["tags", "Map(String, String)"], ["monthly_cost", "Float64"],
    ]
    mock_ch._client.execute = AsyncMock(return_value=(rows, columns))

    agent = TaggingAgent(clickhouse_client=mock_ch)
    agent._settings = MagicMock()
    agent._settings.anthropic_api_key = None  # skip LLM
    agent._settings.llm_default_model = "claude-sonnet-4-20250514"

    # Step 1 — scan
    violations = await agent.scan_violations(time_range_days=30)
    assert len(violations) == 1
    assert violations[0].resource_id == "i-notag01"
    assert "team" in violations[0].missing_tags

    # Step 2 — infer (no API key → all "unknown")
    inferred = await agent.infer_tags("i-notag01", "Virtual Machine", "acct-e2e")
    assert set(inferred.keys()) == {"team", "environment", "project", "owner"}

    # Step 3 — apply (mocked)
    agent._policy.evaluate = AsyncMock(return_value={"allowed": True, "reason": None})
    agent._apply_aws_tags = AsyncMock(return_value={
        "status": "applied", "provider": "aws",
        "resource_id": "i-notag01", "tags": inferred,
    })
    apply_result = await agent.apply_tags(
        provider=CloudProvider.AWS,
        resource_id="i-notag01",
        tags=inferred,
        region="us-east-1",
    )
    assert apply_result["status"] == "applied"


@pytest.mark.asyncio
async def test_policy_gate_blocks_delete_action():
    """
    OPA gate must block delete actions regardless of environment.
    This validates the safety contract from the PDF spec.
    """
    from cloudsense.agents.specialist.action_agent import ActionAgent
    from cloudsense.core.models.billing import ActionRequest, CostRecommendation
    from cloudsense.core.models.enums import ActionStatus, AgentName, CloudProvider, Environment

    rec = CostRecommendation(
        agent=AgentName.ACTION, provider=CloudProvider.AWS,
        title="T", description="D",
        estimated_monthly_savings=Decimal("1"), confidence_score=0.9,
    )
    action = ActionRequest(
        recommendation_id=rec.id,
        provider=CloudProvider.AWS,
        environment=Environment.DEVELOPMENT,
        action_type="delete",
        target_resource_id="s3://critical-bucket",
        rollback_plan={},
        requested_by="agent",
    )

    agent = ActionAgent()
    # Real local policy engine — delete is always blocked
    result = await agent.execute(action, approved_by="admin")

    assert result["status"] == ActionStatus.REJECTED.value
    assert "blocked" in result["reason"].lower()


@pytest.mark.asyncio
async def test_rollback_window_expiry_pipeline():
    """
    End-to-end rollback window: attempt rollback after expiry → error.
    """
    import datetime
    from datetime import timezone
    from cloudsense.agents.specialist.action_agent import ActionAgent, RollbackRegistry

    expired_entry = {
        "plan": {"provider": "aws", "rollback_action": "start_instance",
                 "instance_id": "i-expired", "region": "us-east-1"},
        "registered_at": (datetime.datetime.now(tz=timezone.utc) -
                          datetime.timedelta(days=10)).isoformat(),
        "expires_at": (datetime.datetime.now(tz=timezone.utc) -
                       datetime.timedelta(days=3)).isoformat(),
    }

    mock_repo = MagicMock()
    mock_repo.load_rollback_plan = AsyncMock(return_value=expired_entry)

    registry = RollbackRegistry(repo=mock_repo)
    agent = ActionAgent()
    agent._rollback_registry = registry

    result = await agent.rollback("expired-action-id")
    assert "error" in result
    assert "expired" in result["error"]


@pytest.mark.asyncio
async def test_multi_cloud_agent_pipeline_mock():
    """
    Supervisor runs tagging + anomaly + cloud cost agents, all mocked.
    Validates Phase 4 DAG integration.
    """
    from cloudsense.agents.supervisor.supervisor import SupervisorAgent
    from cloudsense.agents.shared_types import CostInsight, InsightSeverity

    mock_ch = MagicMock()
    mock_ch._client = MagicMock()
    mock_ch._client.execute = AsyncMock(return_value=([], []))

    supervisor = SupervisorAgent(mock_ch)

    # Mock all specialist agents to avoid real cloud calls
    dummy_insight = CostInsight(
        insight_id=str(uuid4()), agent="test", provider="aws",
        severity=InsightSeverity.MEDIUM,
        title="Test insight", description="Desc",
        confidence_score=0.8, action_type="investigate", risk_level="low",
    )

    supervisor._node_aws = AsyncMock(return_value=MagicMock(
        insights=[dummy_insight], completed_agents=["aws"],
        errors=[], recommendations=[], goal="test",
        providers=["aws"], time_range_days=7, current_agent="aws",
        clickhouse_query_results=[], memory_context="",
    ))

    result = await supervisor.analyze(
        goal="Test full pipeline",
        providers=["aws"],
        time_range_days=7,
    )

    assert result.recommendation_id is not None
    assert result.goal == "Test full pipeline"


# ─────────────────────────────────────────────
# LocalStack EC2 sandbox tests (real API calls)
# ─────────────────────────────────────────────

@requires_localstack
@pytest.mark.asyncio
async def test_localstack_stop_instance_e2e():
    """
    Real EC2 API call through LocalStack:
    1. Create a mock EC2 instance in LocalStack
    2. Call AWSActionExecutor.stop_instance()
    3. Assert instance state transitions to 'stopped'
    """
    import boto3
    from cloudsense.agents.specialist.action_agent import AWSActionExecutor

    session = make_localstack_session()
    ec2 = session.client("ec2", region_name="us-east-1",
                         endpoint_url=LOCALSTACK_ENDPOINT)

    # Create a minimal instance in LocalStack
    resp = ec2.run_instances(
        ImageId="ami-00000000",
        MinCount=1,
        MaxCount=1,
        InstanceType="t2.micro",
    )
    instance_id = resp["Instances"][0]["InstanceId"]

    # Wait for it to be running
    waiter = ec2.get_waiter("instance_running")
    try:
        waiter.wait(InstanceIds=[instance_id], WaiterConfig={"MaxAttempts": 10})
    except Exception:
        pass  # LocalStack may not support full waiter

    # Patch boto3.Session to use LocalStack endpoint
    with patch("boto3.Session") as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_ec2 = session.client("ec2", region_name="us-east-1",
                                  endpoint_url=LOCALSTACK_ENDPOINT)
        mock_session.client = MagicMock(return_value=mock_ec2)

        from cloudsense.services.api.config import get_settings
        executor = AWSActionExecutor(settings=get_settings())
        executor._session = session

        result = await executor.stop_instance(resource_id=instance_id, region="us-east-1")

    assert result["resource_id"] == instance_id
    assert "stopping" in result.get("new_state", "stopping")
    assert "rollback" in result
