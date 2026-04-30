"""
Unit tests for OPA policy engine.
"""

from __future__ import annotations

import pytest

from agents.shared_types import RecommendationCategory, RecommendationResult, RiskLevel
from policy.engine import PolicyEngine


class TestPolicyEngine:
    """Test policy evaluation."""

    @pytest.mark.asyncio
    async def test_auto_approve_low_risk(self) -> None:
        engine = PolicyEngine(opa_url="")  # Local mode
        rec = RecommendationResult(
            title="Tag compliance",
            description="Fix tags",
            category=RecommendationCategory.TAG_COMPLIANCE,
            total_projected_monthly_savings=100.0,
            risk_level=RiskLevel.LOW,
        )
        decision = await engine.evaluate_recommendation(rec, environment="development")
        assert decision.allowed is True
        assert "auto-approved" in decision.reason

    @pytest.mark.asyncio
    async def test_deny_production_idle_resource(self) -> None:
        engine = PolicyEngine(opa_url="")
        rec = RecommendationResult(
            title="Stop idle EC2",
            description="Stop instance",
            category=RecommendationCategory.IDLE_RESOURCE,
            total_projected_monthly_savings=500.0,
            risk_level=RiskLevel.MEDIUM,
            requires_approval=True,
        )
        decision = await engine.evaluate_recommendation(rec, environment="production")
        assert decision.allowed is False
        assert "approval" in decision.reason.lower()

    @pytest.mark.asyncio
    async def test_deny_high_spend(self) -> None:
        engine = PolicyEngine(opa_url="")
        rec = RecommendationResult(
            title="Big savings",
            description="Large change",
            category=RecommendationCategory.IDLE_RESOURCE,
            total_projected_monthly_savings=100000.0,
            risk_level=RiskLevel.LOW,
        )
        decision = await engine.evaluate_recommendation(rec, environment="development")
        assert decision.allowed is False
        assert "50K" in decision.reason

    @pytest.mark.asyncio
    async def test_batch_evaluate(self) -> None:
        engine = PolicyEngine(opa_url="")
        recs = [
            RecommendationResult(title="Low", description="d", category=RecommendationCategory.TAG_COMPLIANCE, total_projected_monthly_savings=50, risk_level=RiskLevel.LOW),
            RecommendationResult(title="High", description="d", category=RecommendationCategory.IDLE_RESOURCE, total_projected_monthly_savings=5000, risk_level=RiskLevel.HIGH),
        ]
        decisions = await engine.batch_evaluate(recs, environment="staging")
        assert len(decisions) == 2
        # Low risk tag compliance should be auto-approved in staging
        assert decisions[0].allowed is True

    def test_get_policy_document(self) -> None:
        engine = PolicyEngine(opa_url="")
        doc = engine.get_policy_document()
        assert "package cloudsense" in doc
        assert "allow" in doc
        assert "deny" in doc
