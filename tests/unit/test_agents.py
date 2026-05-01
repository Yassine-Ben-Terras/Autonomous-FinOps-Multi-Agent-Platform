"""Tests for agent engine."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from cloudsense.agents.shared_types import CostInsight, InsightSeverity, AgentState
from cloudsense.agents.specialist.aws_agent import AWSCostAgent
from cloudsense.agents.supervisor.supervisor import SupervisorAgent

@pytest.mark.asyncio
async def test_supervisor_analysis():
    mock_ch = MagicMock()
    mock_ch._client = MagicMock()
    mock_ch._client.execute = AsyncMock(return_value=([], []))

    supervisor = SupervisorAgent(mock_ch)
    result = await supervisor.analyze(goal="Test", providers=["aws"], time_range_days=7)
    assert result.recommendation_id is not None
    assert result.goal == "Test"

@pytest.mark.asyncio
async def test_aws_agent_heuristics():
    mock_ch = MagicMock()
    mock_ch._client = MagicMock()
    mock_ch._client.execute = AsyncMock(return_value=(
        [["i-123", "web-server", "us-east-1", 150.0, 1.5, 0.5]],
        [["resource_id", "String"], ["resource_name", "String"], ["region_id", "String"],
         ["monthly_cost", "Float64"], ["avg_daily_usage", "Float64"], ["usage_variance", "Float64"]]
    ))

    agent = AWSCostAgent(mock_ch)
    insights = await agent.analyze(time_range_days=30)
    assert len(insights) > 0
    assert insights[0].provider == "aws"
    assert insights[0].severity == InsightSeverity.HIGH
