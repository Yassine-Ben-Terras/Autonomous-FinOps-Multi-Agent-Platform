"""
Recommendation Engine — Cost Optimization Logic

Core engine that transforms raw cost insights into prioritized,
actionable recommendations. Handles deduplication, conflict resolution,
and savings calculation.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from agents.shared_types import (
    CostInsight,
    RecommendationCategory,
    RecommendationResult,
    RiskLevel,
)

logger = logging.getLogger(__name__)


class RecommendationEngine:
    """Transforms insights into prioritized recommendations.

    Handles:
    - Deduplication of similar insights
    - Savings aggregation per recommendation
    - Conflict detection and resolution
    - Priority scoring (savings × confidence / risk)
    """

    # Risk weights for priority calculation
    RISK_WEIGHTS = {
        RiskLevel.LOW: 1.0,
        RiskLevel.MEDIUM: 0.7,
        RiskLevel.HIGH: 0.4,
        RiskLevel.CRITICAL: 0.1,
    }

    def __init__(self) -> None:
        self._insight_cache: dict[str, CostInsight] = {}

    def process_insights(
        self,
        insights: list[CostInsight],
    ) -> list[RecommendationResult]:
        """Process a batch of insights into recommendations.

        Args:
            insights: Raw insights from specialist agents

        Returns:
            Prioritized list of recommendations
        """
        # Deduplicate
        unique = self._deduplicate(insights)
        logger.info("Deduped %d insights to %d unique", len(insights), len(unique))

        # Group by category
        grouped = self._group_by_category(unique)

        # Build recommendations
        recommendations: list[RecommendationResult] = []
        for category, group in grouped.items():
            rec = self._build_recommendation(category, group)
            if rec:
                recommendations.append(rec)

        # Score and sort
        recommendations = self._prioritize(recommendations)

        return recommendations

    def _deduplicate(self, insights: list[CostInsight]) -> list[CostInsight]:
        """Remove duplicate insights based on resource_id + category."""
        seen: set[str] = set()
        unique: list[CostInsight] = []

        for insight in insights:
            key = f"{insight.provider}:{insight.category.value}:{insight.resource_id or insight.title}"
            if key not in seen:
                seen.add(key)
                unique.append(insight)

        return unique

    def _group_by_category(
        self,
        insights: list[CostInsight],
    ) -> dict[RecommendationCategory, list[CostInsight]]:
        """Group insights by recommendation category."""
        grouped: dict[RecommendationCategory, list[CostInsight]] = {}
        for insight in insights:
            cat = insight.category
            if cat not in grouped:
                grouped[cat] = []
            grouped[cat].append(insight)
        return grouped

    def _build_recommendation(
        self,
        category: RecommendationCategory,
        insights: list[CostInsight],
    ) -> RecommendationResult | None:
        """Build a single recommendation from a group of insights."""
        if not insights:
            return None

        providers = list(set(i.provider for i in insights))
        total_savings = sum(i.projected_monthly_savings for i in insights)
        avg_confidence = sum(i.confidence_score for i in insights) / len(insights)

        # Determine overall risk
        risk_levels = [i.risk_level for i in insights]
        overall_risk = max(risk_levels, key=lambda r: list(RiskLevel).index(r))

        # Build description
        titles = [i.title for i in insights[:3]]
        description = "\n".join(f"- {t}" for t in titles)
        if len(insights) > 3:
            description += f"\n- ... and {len(insights) - 3} more"

        # Determine effort
        effort_map = {
            RecommendationCategory.IDLE_RESOURCE: "low",
            RecommendationCategory.TAG_COMPLIANCE: "low",
            RecommendationCategory.RIGHT_SIZE: "medium",
            RecommendationCategory.COMMITMENT_GAP: "medium",
            RecommendationCategory.STORAGE_OPTIMIZATION: "medium",
            RecommendationCategory.ORPHANED_RESOURCE: "low",
        }
        effort = effort_map.get(category, "medium")

        # Build actions
        actions = self._generate_actions(category, insights)

        return RecommendationResult(
            title=f"{category.value.replace('_', ' ').title()}: {len(insights)} findings",
            description=description,
            category=category,
            providers_involved=providers,
            total_projected_monthly_savings=round(total_savings, 2),
            implementation_effort=effort,
            risk_level=overall_risk,
            actions=actions,
            requires_approval=overall_risk in (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL),
        )

    def _generate_actions(
        self,
        category: RecommendationCategory,
        insights: list[CostInsight],
    ) -> list[str]:
        """Generate recommended actions based on category."""
        actions_map = {
            RecommendationCategory.IDLE_RESOURCE: [
                "Review identified resources in CloudSense dashboard",
                "Notify owners via Slack with 7-day grace period",
                "Auto-stop non-prod resources after grace period",
                "Archive and terminate confirmed idle resources",
            ],
            RecommendationCategory.RIGHT_SIZE: [
                "Review utilization metrics for flagged resources",
                "Test smaller configurations in staging environment",
                "Schedule maintenance window for resizing",
                "Monitor performance for 48h post-change",
            ],
            RecommendationCategory.COMMITMENT_GAP: [
                "Export 6-month usage trends",
                "Model commitment scenarios (1yr vs 3yr)",
                "Purchase commitments for stable workloads",
                "Set up utilization alerts",
            ],
            RecommendationCategory.TAG_COMPLIANCE: [
                "Deploy OPA tagging policies",
                "Run automated tag inference",
                "Enforce tags at provisioning time",
                "Monthly compliance reporting",
            ],
            RecommendationCategory.ORPHANED_RESOURCE: [
                "Identify resource dependencies",
                "Create snapshots before deletion",
                "Delete confirmed orphaned resources",
                "Implement lifecycle policies",
            ],
        }
        return actions_map.get(category, ["Review findings in dashboard", "Implement recommended changes"])

    def _prioritize(
        self,
        recommendations: list[RecommendationResult],
    ) -> list[RecommendationResult]:
        """Score and sort recommendations by priority.

        Priority score = projected_savings × confidence / risk_weight
        """
        def score(rec: RecommendationResult) -> float:
            risk_weight = self.RISK_WEIGHTS.get(rec.risk_level, 0.5)
            # Normalize savings (cap at $100K to prevent outliers dominating)
            normalized_savings = min(rec.total_projected_monthly_savings, 100_000)
            return normalized_savings * risk_weight

        recommendations.sort(key=score, reverse=True)
        return recommendations

    def calculate_roi(
        self,
        recommendation: RecommendationResult,
        implementation_hours: float = 4.0,
        hourly_cost: float = 100.0,
    ) -> dict[str, Any]:
        """Calculate ROI for a recommendation.

        Args:
            recommendation: The recommendation to analyze
            implementation_hours: Estimated engineering hours
            hourly_cost: Fully-loaded hourly cost

        Returns:
            ROI analysis dict
        """
        monthly_savings = recommendation.total_projected_monthly_savings
        implementation_cost = implementation_hours * hourly_cost
        payback_months = implementation_cost / monthly_savings if monthly_savings > 0 else float("inf")
        annual_savings = monthly_savings * 12
        roi = ((annual_savings - implementation_cost) / implementation_cost * 100) if implementation_cost > 0 else 0

        return {
            "monthly_savings": round(monthly_savings, 2),
            "annual_savings": round(annual_savings, 2),
            "implementation_cost": round(implementation_cost, 2),
            "payback_months": round(payback_months, 2) if payback_months != float("inf") else None,
            "roi_percent": round(roi, 2),
            "recommendation_id": recommendation.recommendation_id,
        }

    def detect_conflicts(
        self,
        recommendations: list[RecommendationResult],
    ) -> list[dict[str, Any]]:
        """Detect conflicting recommendations across providers.

        Example conflict: AWS agent recommends scaling up a database
        while Azure agent recommends migrating to Azure SQL.
        """
        conflicts: list[dict[str, Any]] = []

        # Check for cross-cloud redundancy
        db_recs = [r for r in recommendations if r.category in (
            RecommendationCategory.RIGHT_SIZE,
            RecommendationCategory.IDLE_RESOURCE,
        )]

        for i, rec1 in enumerate(db_recs):
            for rec2 in db_recs[i + 1:]:
                if rec1.category == rec2.category and rec1.providers_involved != rec2.providers_involved:
                    conflicts.append({
                        "type": "cross_cloud_similarity",
                        "description": (
                            f"Similar {rec1.category.value} recommendations "
                            f"across {', '.join(rec1.providers_involved)} and "
                            f"{', '.join(rec2.providers_involved)}"
                        ),
                        "recommendation_ids": [rec1.recommendation_id, rec2.recommendation_id],
                    })

        return conflicts
