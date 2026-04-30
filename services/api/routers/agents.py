"""
Agent API endpoints — Trigger and monitor agent analysis runs.

Provides REST interface to the LangGraph supervisor and specialist agents.
All endpoints are async and return immediately with a job ID.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from agents.shared_types import (
    AgentStatus,
    SupervisorState,
    state_from_dict,
    state_to_dict,
)
from agents.supervisor.supervisor import SupervisorAgent
from bot.slack_bot import SlackBot
from recommendations.engine import RecommendationEngine

logger = logging.getLogger(__name__)

router = APIRouter()

# In-memory store for analysis jobs
_analysis_jobs: dict[str, SupervisorState] = {}


class AnalysisRequest(BaseModel):
    """Request body for triggering analysis."""

    goal: str = Field(
        default="Find cost optimization opportunities across all clouds",
        description="Natural language analysis goal",
    )
    providers: list[str] | None = Field(
        default=None,
        description="Providers to analyze (aws, azure, gcp). Null = all.",
    )
    notify_slack: bool = Field(
        default=False,
        description="Send results to Slack when complete",
    )


class AnalysisResponse(BaseModel):
    """Response from analysis trigger."""

    session_id: str
    status: str
    goal: str
    providers: list[str]
    started_at: str


class AnalysisResultResponse(BaseModel):
    """Full analysis results."""

    session_id: str
    status: str
    goal: str
    insight_count: int
    recommendation_count: int
    total_projected_monthly_savings: float
    providers_analyzed: list[str]
    recommendations: list[dict[str, Any]]
    report_preview: str
    created_at: str
    completed_at: str | None


async def _run_analysis(session_id: str, request: AnalysisRequest) -> None:
    """Background task to run agent analysis."""
    try:
        supervisor = SupervisorAgent()
        state = await supervisor.analyze(
            goal=request.goal,
            providers=request.providers,
        )
        _analysis_jobs[session_id] = state

        # Optionally notify Slack
        if request.notify_slack:
            slack = SlackBot()
            if slack.is_configured() and state.recommendations:
                await slack.send_digest(
                    state.recommendations,
                    period="analysis",
                )

        logger.info("Analysis %s complete: %d insights", session_id, len(state.insights))

    except Exception as exc:
        logger.error("Analysis %s failed: %s", session_id, exc)
        # Store failed state
        failed_state = SupervisorState(
            session_id=session_id,
            goal=request.goal,
            status=AgentStatus.FAILED,
        )
        _analysis_jobs[session_id] = failed_state


@router.post("/analyze", response_model=AnalysisResponse)
async def trigger_analysis(
    request: AnalysisRequest,
    background_tasks: BackgroundTasks,
) -> AnalysisResponse:
    """Trigger a multi-cloud cost analysis.

    The analysis runs in the background. Use GET /status/{session_id} to check progress.
    """
    supervisor = SupervisorAgent()
    # Pre-init to get session ID
    providers = request.providers or ["aws", "azure", "gcp"]
    session_id = f"analysis-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

    # Initialize with pending state
    initial_state = SupervisorState(
        session_id=session_id,
        goal=request.goal,
        status=AgentStatus.PENDING,
    )
    _analysis_jobs[session_id] = initial_state

    background_tasks.add_task(_run_analysis, session_id, request)

    return AnalysisResponse(
        session_id=session_id,
        status="pending",
        goal=request.goal,
        providers=providers,
        started_at=datetime.utcnow().isoformat(),
    )


@router.get("/status/{session_id}", response_model=AnalysisResultResponse)
async def get_analysis_status(session_id: str) -> AnalysisResultResponse:
    """Get the status and results of an analysis job."""
    if session_id not in _analysis_jobs:
        raise HTTPException(status_code=404, detail="Analysis session not found")

    state = _analysis_jobs[session_id]

    return AnalysisResultResponse(
        session_id=state.session_id,
        status=state.status.value,
        goal=state.goal,
        insight_count=len(state.insights),
        recommendation_count=len(state.recommendations),
        total_projected_monthly_savings=sum(
            r.total_projected_monthly_savings for r in state.recommendations
        ),
        providers_analyzed=list(set(t.provider for t in state.tasks if t.provider)),
        recommendations=[
            {
                "id": r.recommendation_id,
                "title": r.title,
                "category": r.category.value,
                "savings": r.total_projected_monthly_savings,
                "risk": r.risk_level.value,
                "providers": r.providers_involved,
                "requires_approval": r.requires_approval,
                "actions": r.actions,
            }
            for r in state.recommendations
        ],
        report_preview=state.final_report[:2000] if state.final_report else "",
        created_at=state.created_at.isoformat(),
        completed_at=state.updated_at.isoformat() if state.status in (AgentStatus.COMPLETED, AgentStatus.FAILED) else None,
    )


@router.get("/history")
async def list_analysis_jobs() -> list[dict[str, Any]]:
    """List recent analysis jobs."""
    jobs = []
    for session_id, state in sorted(
        _analysis_jobs.items(),
        key=lambda x: x[1].created_at,
        reverse=True,
    )[:20]:
        jobs.append({
            "session_id": state.session_id,
            "status": state.status.value,
            "goal": state.goal[:100],
            "insight_count": len(state.insights),
            "recommendation_count": len(state.recommendations),
            "created_at": state.created_at.isoformat(),
        })
    return jobs


@router.post("/quick/{provider}")
async def quick_provider_analysis(provider: str) -> AnalysisResponse:
    """Quick analysis for a single provider."""
    if provider not in ("aws", "azure", "gcp"):
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    request = AnalysisRequest(
        goal=f"Quick cost analysis for {provider.upper()}",
        providers=[provider],
    )
    return await trigger_analysis(request, BackgroundTasks())


@router.get("/recommendations/{recommendation_id}/roi")
async def get_recommendation_roi(recommendation_id: str) -> dict[str, Any]:
    """Calculate ROI for a specific recommendation."""
    rec_engine = RecommendationEngine()

    # Find the recommendation
    for state in _analysis_jobs.values():
        for rec in state.recommendations:
            if rec.recommendation_id == recommendation_id:
                roi = rec_engine.calculate_roi(rec)
                return roi

    raise HTTPException(status_code=404, detail="Recommendation not found")


@router.get("/insights")
async def list_insights(
    provider: str | None = None,
    category: str | None = None,
) -> list[dict[str, Any]]:
    """List all insights from completed analyses."""
    all_insights = []
    for state in _analysis_jobs.values():
        for insight in state.insights:
            if provider and insight.provider != provider:
                continue
            if category and insight.category.value != category:
                continue
            all_insights.append({
                "insight_id": insight.insight_id,
                "provider": insight.provider,
                "category": insight.category.value,
                "title": insight.title,
                "current_cost": insight.current_monthly_cost,
                "projected_savings": insight.projected_monthly_savings,
                "confidence": insight.confidence_score,
                "risk": insight.risk_level.value,
                "created_at": insight.created_at.isoformat(),
            })

    # Sort by projected savings
    all_insights.sort(key=lambda x: x["projected_savings"], reverse=True)
    return all_insights[:100]


@router.get("/supervisor/graph")
async def get_supervisor_graph() -> dict[str, Any]:
    """Get the LangGraph DAG structure of the supervisor."""
    supervisor = SupervisorAgent()
    return supervisor.build_graph()
