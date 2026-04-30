"""
Unit tests for specialist agents and supervisor.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.shared_types import (
    AgentStatus,
    AgentTask,
    CostInsight,
    RecommendationCategory,
    RiskLevel,
)
from agents.specialist.aws_agent import AWSCostAgent
from agents.specialist.azure_agent import AzureCostAgent
from agents.specialist.gcp_agent import GCPCostAgent
from agents.supervisor.supervisor import SupervisorAgent
from recommendations.engine import RecommendationEngine


class TestAWSCostAgent:
    """Test AWS specialist agent."""

    @pytest.mark.asyncio
    async def test_execute_returns_task(self) -> None:
        agent = AWSCostAgent()
        task = AgentTask(
            agent_type="aws",
            goal="Test AWS analysis",
            provider="aws",
        )

        # Mock the tools
        with patch.object(agent.tools, "get_provider_spend", new_callable=AsyncMock) as mock_spend, \
             patch.object(agent.tools, "get_service_breakdown", new_callable=AsyncMock) as mock_svc, \
             patch.object(agent.tools, "get_idle_resources", new_callable=AsyncMock) as mock_idle, \
             patch.object(agent.tools, "get_commitment_coverage", new_callable=AsyncMock) as mock_commit, \
             patch.object(agent.tools, "get_tag_coverage", new_callable=AsyncMock) as mock_tags, \
             patch.object(agent.tools, "get_cost_by_region", new_callable=AsyncMock) as mock_regions:

            mock_spend.return_value = {
                "total_cost": 5000.0,
                "total_savings": 200.0,
                "period_days": 30,
                "service_count": 10,
            }
            mock_svc.return_value = [
                {"service_name": "EC2", "cost": 2000.0},
                {"service_name": "S3", "cost": 500.0},
            ]
            mock_idle.return_value = [
                {"resource_id": "i-123", "total_cost": 150.0},
            ]
            mock_commit.return_value = {
                "commitment_coverage_pct": 45.0,
                "total_effective_cost": 5000.0,
                "opportunity": 2000.0,
            }
            mock_tags.return_value = {
                "team_tag_coverage_pct": 60.0,
                "untagged_records": 100,
            }
            mock_regions.return_value = [
                {"region": "us-east-1", "cost": 3000.0},
            ]

            result = await agent.execute(task)

        assert result.status == AgentStatus.COMPLETED
        assert len(result.result) >= 3  # Should have multiple insights
        assert result.completed_at is not None

    @pytest.mark.asyncio
    async def test_execute_with_no_data(self) -> None:
        agent = AWSCostAgent()
        task = AgentTask(agent_type="aws", goal="Test", provider="aws")

        with patch.object(agent.tools, "get_provider_spend", new_callable=AsyncMock) as mock_spend, \
             patch.object(agent.tools, "get_service_breakdown", new_callable=AsyncMock) as mock_svc, \
             patch.object(agent.tools, "get_idle_resources", new_callable=AsyncMock) as mock_idle, \
             patch.object(agent.tools, "get_commitment_coverage", new_callable=AsyncMock) as mock_commit, \
             patch.object(agent.tools, "get_tag_coverage", new_callable=AsyncMock) as mock_tags, \
             patch.object(agent.tools, "get_cost_by_region", new_callable=AsyncMock) as mock_regions:

            mock_spend.return_value = {"total_cost": 0, "total_savings": 0, "period_days": 30, "service_count": 0}
            mock_svc.return_value = []
            mock_idle.return_value = []
            mock_commit.return_value = {"commitment_coverage_pct": 0, "total_effective_cost": 0, "opportunity": 0}
            mock_tags.return_value = {"team_tag_coverage_pct": 0, "untagged_records": 0}
            mock_regions.return_value = []

            result = await agent.execute(task)

        assert result.status == AgentStatus.COMPLETED


class TestSupervisorAgent:
    """Test supervisor orchestration."""

    @pytest.mark.asyncio
    async def test_analyze_single_provider(self) -> None:
        supervisor = SupervisorAgent()

        with patch.object(supervisor.aws_agent, "execute", new_callable=AsyncMock) as mock_aws:
            mock_aws.return_value = AgentTask(
                agent_type="aws",
                goal="test",
                provider="aws",
                status=AgentStatus.COMPLETED,
                result=[
                    CostInsight(
                        provider="aws",
                        category=RecommendationCategory.IDLE_RESOURCE,
                        title="Test insight",
                        description="Test",
                        projected_monthly_savings=100.0,
                        risk_level=RiskLevel.LOW,
                    ),
                ],
            )

            state = await supervisor.analyze("Test analysis", providers=["aws"])

        assert state.status == AgentStatus.COMPLETED
        assert len(state.insights) >= 1
        assert state.session_id is not None

    def test_synthesize_recommendations(self) -> None:
        supervisor = SupervisorAgent()
        from agents.shared_types import SupervisorState

        state = SupervisorState(goal="test")
        state.insights = [
            CostInsight(
                provider="aws",
                category=RecommendationCategory.IDLE_RESOURCE,
                title="Idle EC2",
                description="Found idle instances",
                projected_monthly_savings=500.0,
                risk_level=RiskLevel.LOW,
            ),
            CostInsight(
                provider="aws",
                category=RecommendationCategory.COMMITMENT_GAP,
                title="RI Gap",
                description="Low coverage",
                projected_monthly_savings=1000.0,
                risk_level=RiskLevel.LOW,
            ),
        ]

        recs = supervisor._synthesize_recommendations(state)
        assert len(recs) >= 1
        # Should be sorted by savings
        if len(recs) >= 2:
            assert recs[0].total_projected_monthly_savings >= recs[1].total_projected_monthly_savings

    def test_graph_structure(self) -> None:
        supervisor = SupervisorAgent()
        graph = supervisor.build_graph()
        assert "nodes" in graph
        assert "edges" in graph
        assert "dispatch_aws" in graph["nodes"]
        assert "synthesize" in graph["nodes"]


class TestRecommendationEngine:
    """Test recommendation engine logic."""

    def test_deduplicate_insights(self) -> None:
        engine = RecommendationEngine()
        insights = [
            CostInsight(provider="aws", category=RecommendationCategory.IDLE_RESOURCE, title="Idle 1", description="d", resource_id="i-1"),
            CostInsight(provider="aws", category=RecommendationCategory.IDLE_RESOURCE, title="Idle 1 dup", description="d", resource_id="i-1"),
            CostInsight(provider="aws", category=RecommendationCategory.IDLE_RESOURCE, title="Idle 2", description="d", resource_id="i-2"),
        ]
        unique = engine._deduplicate(insights)
        assert len(unique) == 2

    def test_prioritize_recommendations(self) -> None:
        engine = RecommendationEngine()
        from agents.shared_types import RecommendationResult

        recs = [
            RecommendationResult(title="Low", description="d", category=RecommendationCategory.IDLE_RESOURCE, total_projected_monthly_savings=100, risk_level=RiskLevel.LOW),
            RecommendationResult(title="High", description="d", category=RecommendationCategory.IDLE_RESOURCE, total_projected_monthly_savings=1000, risk_level=RiskLevel.LOW),
            RecommendationResult(title="Med", description="d", category=RecommendationCategory.IDLE_RESOURCE, total_projected_monthly_savings=500, risk_level=RiskLevel.MEDIUM),
        ]
        sorted_recs = engine._prioritize(recs)
        assert sorted_recs[0].title == "High"  # Highest savings first

    def test_calculate_roi(self) -> None:
        engine = RecommendationEngine()
        from agents.shared_types import RecommendationResult

        rec = RecommendationResult(
            title="Test",
            description="d",
            category=RecommendationCategory.IDLE_RESOURCE,
            total_projected_monthly_savings=1000.0,
        )
        roi = engine.calculate_roi(rec, implementation_hours=8, hourly_cost=150)
        assert roi["monthly_savings"] == 1000.0
        assert roi["annual_savings"] == 12000.0
        assert roi["implementation_cost"] == 1200.0
        assert roi["roi_percent"] > 0

    def test_detect_conflicts(self) -> None:
        engine = RecommendationEngine()
        from agents.shared_types import RecommendationResult

        recs = [
            RecommendationResult(title="AWS Resize", description="d", category=RecommendationCategory.RIGHT_SIZE, providers_involved=["aws"]),
            RecommendationResult(title="Azure Resize", description="d", category=RecommendationCategory.RIGHT_SIZE, providers_involved=["azure"]),
        ]
        conflicts = engine.detect_conflicts(recs)
        assert len(conflicts) >= 1
