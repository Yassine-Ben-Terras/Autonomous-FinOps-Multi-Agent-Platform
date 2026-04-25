"""
Tests for billing domain models: resources, recommendations, actions, anomalies,
forecasts, and tag violations.
Pure unit tests — no external services.
"""
import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import ValidationError

from cloudsense.core.models.enums import (
    ActionStatus,
    AgentName,
    CloudProvider,
    Environment,
    ResourceStatus,
)
from cloudsense.core.models.billing import (
    ActionRequest,
    CloudResource,
    CostAnomaly,
    CostForecast,
    CostRecommendation,
    TagViolation,
)


class TestCloudResource:
    def test_default_status_is_unknown(self, now):
        resource = CloudResource(
            provider=CloudProvider.AWS,
            resource_id="i-0abc",
            resource_type="EC2 Instance",
            billing_account_id="123456",
        )
        assert resource.status == ResourceStatus.UNKNOWN

    def test_monthly_cost_cannot_be_negative(self):
        with pytest.raises(ValidationError):
            CloudResource(
                provider=CloudProvider.AWS,
                resource_id="i-0abc",
                resource_type="EC2 Instance",
                billing_account_id="123456",
                monthly_cost=Decimal("-1.00"),
            )

    def test_is_immutable(self):
        r = CloudResource(
            provider=CloudProvider.AWS,
            resource_id="i-0abc",
            resource_type="EC2 Instance",
            billing_account_id="123456",
        )
        with pytest.raises(Exception):
            r.status = ResourceStatus.IDLE


class TestCostRecommendation:
    def test_annual_savings_is_12x_monthly(self, base_recommendation):
        assert base_recommendation.annual_savings == Decimal("540.00")

    def test_confidence_must_be_0_to_1(self):
        with pytest.raises(ValidationError):
            CostRecommendation(
                agent=AgentName.AWS_COST,
                provider=CloudProvider.AWS,
                title="Test",
                description="Test recommendation",
                estimated_monthly_savings=Decimal("10.00"),
                confidence_score=1.5,  # invalid
            )

    def test_estimated_savings_cannot_be_negative(self):
        with pytest.raises(ValidationError):
            CostRecommendation(
                agent=AgentName.AWS_COST,
                provider=CloudProvider.AWS,
                title="Test",
                description="Test recommendation",
                estimated_monthly_savings=Decimal("-5.00"),
                confidence_score=0.8,
            )

    def test_expiry_must_be_after_creation(self, now):
        with pytest.raises(ValidationError, match="expires_at must be after created_at"):
            CostRecommendation(
                agent=AgentName.AWS_COST,
                provider=CloudProvider.AWS,
                title="Test",
                description="Test recommendation",
                estimated_monthly_savings=Decimal("10.00"),
                confidence_score=0.8,
                created_at=now,
                expires_at=now - timedelta(days=1),
            )


class TestActionRequest:
    def test_production_requires_human_approval(self, base_recommendation):
        action = ActionRequest(
            recommendation_id=base_recommendation.id,
            provider=CloudProvider.AWS,
            environment=Environment.PRODUCTION,
            action_type="rightsize",
            target_resource_id="i-0abc",
            rollback_plan={"restore": "m5.xlarge"},
            requested_by="agent",
        )
        assert action.requires_human_approval is True

    def test_development_does_not_require_human_approval(self, base_action_request):
        assert base_action_request.requires_human_approval is False

    def test_pending_action_is_not_terminal(self, base_action_request):
        assert base_action_request.is_terminal is False

    def test_completed_action_is_terminal(self, base_recommendation):
        action = ActionRequest(
            recommendation_id=base_recommendation.id,
            provider=CloudProvider.AWS,
            environment=Environment.DEVELOPMENT,
            action_type="rightsize",
            target_resource_id="i-0abc",
            rollback_plan={},
            requested_by="agent",
            status=ActionStatus.COMPLETED,
        )
        assert action.is_terminal is True

    def test_rolled_back_action_is_terminal(self, base_recommendation):
        action = ActionRequest(
            recommendation_id=base_recommendation.id,
            provider=CloudProvider.AWS,
            environment=Environment.DEVELOPMENT,
            action_type="rightsize",
            target_resource_id="i-0abc",
            rollback_plan={},
            requested_by="agent",
            status=ActionStatus.ROLLED_BACK,
        )
        assert action.is_terminal is True


class TestCostAnomaly:
    def test_cost_delta_calculation(self, now):
        anomaly = CostAnomaly(
            provider=CloudProvider.AWS,
            billing_account_id="123456",
            period_start=now,
            period_end=now + timedelta(hours=1),
            service_name="Amazon EC2",
            expected_cost=Decimal("100.00"),
            actual_cost=Decimal("250.00"),
            anomaly_score=0.85,
        )
        assert anomaly.cost_delta == Decimal("150.00")

    def test_percentage_increase(self, now):
        anomaly = CostAnomaly(
            provider=CloudProvider.AWS,
            billing_account_id="123456",
            period_start=now,
            period_end=now + timedelta(hours=1),
            expected_cost=Decimal("100.00"),
            actual_cost=Decimal("150.00"),
            anomaly_score=0.7,
        )
        assert anomaly.percentage_increase == Decimal("50.00")

    def test_is_significant_true(self, now):
        anomaly = CostAnomaly(
            provider=CloudProvider.AWS,
            billing_account_id="123456",
            period_start=now,
            period_end=now + timedelta(hours=1),
            expected_cost=Decimal("100.00"),
            actual_cost=Decimal("300.00"),
            anomaly_score=0.9,
        )
        assert anomaly.is_significant is True

    def test_is_significant_false_low_score(self, now):
        """High cost delta but low anomaly score → not significant."""
        anomaly = CostAnomaly(
            provider=CloudProvider.AWS,
            billing_account_id="123456",
            period_start=now,
            period_end=now + timedelta(hours=1),
            expected_cost=Decimal("100.00"),
            actual_cost=Decimal("300.00"),
            anomaly_score=0.3,
        )
        assert anomaly.is_significant is False

    def test_percentage_increase_zero_expected_cost(self, now):
        """Division by zero guard: returns 100% when expected cost is 0."""
        anomaly = CostAnomaly(
            provider=CloudProvider.GCP,
            billing_account_id="my-project",
            period_start=now,
            period_end=now + timedelta(hours=1),
            expected_cost=Decimal("0"),
            actual_cost=Decimal("50.00"),
            anomaly_score=0.6,
        )
        assert anomaly.percentage_increase == Decimal("100")


class TestCostForecast:
    def test_valid_forecast(self, now):
        forecast = CostForecast(
            provider=CloudProvider.AWS,
            forecast_period_days=30,
            forecast_start=now,
            forecast_end=now + timedelta(days=30),
            predicted_cost=Decimal("5000.00"),
            lower_bound=Decimal("4500.00"),
            upper_bound=Decimal("5500.00"),
        )
        assert forecast.confidence_range == Decimal("1000.00")

    def test_lower_bound_cannot_exceed_predicted(self, now):
        with pytest.raises(ValidationError, match="lower_bound cannot exceed predicted_cost"):
            CostForecast(
                provider=CloudProvider.AWS,
                forecast_period_days=30,
                forecast_start=now,
                forecast_end=now + timedelta(days=30),
                predicted_cost=Decimal("1000.00"),
                lower_bound=Decimal("1200.00"),   # invalid
                upper_bound=Decimal("1500.00"),
            )

    def test_upper_bound_cannot_be_less_than_predicted(self, now):
        with pytest.raises(ValidationError, match="upper_bound cannot be less than predicted_cost"):
            CostForecast(
                provider=CloudProvider.AWS,
                forecast_period_days=30,
                forecast_start=now,
                forecast_end=now + timedelta(days=30),
                predicted_cost=Decimal("1000.00"),
                lower_bound=Decimal("800.00"),
                upper_bound=Decimal("900.00"),    # invalid
            )

    def test_budget_breach_risk_true(self, now):
        forecast = CostForecast(
            provider=CloudProvider.AWS,
            forecast_period_days=30,
            forecast_start=now,
            forecast_end=now + timedelta(days=30),
            predicted_cost=Decimal("5000.00"),
            lower_bound=Decimal("4500.00"),
            upper_bound=Decimal("6000.00"),
            budget_limit=Decimal("5500.00"),
        )
        assert forecast.budget_breach_risk is True

    def test_budget_breach_risk_none_limit(self, now):
        forecast = CostForecast(
            provider=CloudProvider.AWS,
            forecast_period_days=30,
            forecast_start=now,
            forecast_end=now + timedelta(days=30),
            predicted_cost=Decimal("5000.00"),
            lower_bound=Decimal("4500.00"),
            upper_bound=Decimal("5500.00"),
        )
        assert forecast.budget_breach_risk is False


class TestTagViolation:
    def test_severity_none(self):
        v = TagViolation(
            provider=CloudProvider.AWS,
            resource_id="i-0abc",
            billing_account_id="123456",
        )
        assert v.severity == "none"

    def test_severity_low(self):
        v = TagViolation(
            provider=CloudProvider.AWS,
            resource_id="i-0abc",
            billing_account_id="123456",
            missing_tags=["team"],
        )
        assert v.severity == "low"

    def test_severity_medium(self):
        v = TagViolation(
            provider=CloudProvider.AWS,
            resource_id="i-0abc",
            billing_account_id="123456",
            missing_tags=["team", "environment", "project"],
        )
        assert v.severity == "medium"

    def test_severity_high(self):
        v = TagViolation(
            provider=CloudProvider.AWS,
            resource_id="i-0abc",
            billing_account_id="123456",
            missing_tags=["team", "environment", "project", "owner", "cost-center", "app"],
        )
        assert v.severity == "high"
