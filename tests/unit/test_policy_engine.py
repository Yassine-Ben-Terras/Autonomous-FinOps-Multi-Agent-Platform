"""Tests for OPA Policy Engine."""
import pytest
from cloudsense.agents.shared_types import CostInsight, InsightSeverity, InsightStatus
from cloudsense.policy.engine import PolicyEngine

@pytest.mark.asyncio
async def test_policy_allows_investigate():
    engine = PolicyEngine(opa_url="http://invalid:9999")
    insight = CostInsight(
        insight_id="test-1", agent="test", provider="aws",
        severity=InsightSeverity.HIGH, title="Test",
        description="Test", action_type="investigate", risk_level="low",
        confidence_score=0.9)
    result = await engine.evaluate(insight)
    assert result["allowed"] is True

@pytest.mark.asyncio
async def test_policy_denies_delete():
    engine = PolicyEngine(opa_url="http://invalid:9999")
    insight = CostInsight(
        insight_id="test-2", agent="test", provider="aws",
        severity=InsightSeverity.CRITICAL, title="Test",
        description="Test", action_type="delete", risk_level="high",
        confidence_score=0.9)
    result = await engine.evaluate(insight)
    assert result["allowed"] is False
    assert "permanently blocked" in result["reason"]
