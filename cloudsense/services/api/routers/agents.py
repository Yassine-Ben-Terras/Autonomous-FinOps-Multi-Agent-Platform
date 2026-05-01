"""Agent Engine API (/api/v1/agents/*)."""
from __future__ import annotations
from typing import Any
from uuid import uuid4
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from cloudsense.agents.supervisor.supervisor import SupervisorAgent
from cloudsense.services.api.config import Settings, get_settings
from cloudsense.services.api.deps import require_auth
from cloudsense.services.db.clickhouse import ClickHouseClient

router = APIRouter(prefix="/agents", tags=["Agents"])
_agent_jobs: dict[str, dict[str, Any]] = {}

class AnalyzeRequest(BaseModel):
    goal: str = Field(default="Find cost optimization opportunities", max_length=500)
    providers: list[str] = Field(default_factory=lambda: ["aws", "azure", "gcp"])
    time_range_days: int = Field(default=30, ge=7, le=90)

class QuickAnalyzeRequest(BaseModel):
    goal: str = Field(default="Quick cost analysis", max_length=500)
    time_range_days: int = Field(default=30, ge=7, le=90)

@router.post("/analyze", response_model=dict[str, Any])
async def trigger_analysis(payload: AnalyzeRequest, background_tasks: BackgroundTasks,
                           auth: str = Depends(require_auth), settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    job_id = str(uuid4())
    _agent_jobs[job_id] = {"id": job_id, "status": "queued", "goal": payload.goal,
                           "providers": payload.providers, "time_range_days": payload.time_range_days,
                           "result": None, "errors": []}
    background_tasks.add_task(_run_analysis, job_id=job_id, goal=payload.goal,
                              providers=payload.providers, days=payload.time_range_days, settings=settings)
    return {"job_id": job_id, "status": "queued"}

async def _run_analysis(job_id: str, goal: str, providers: list[str], days: int, settings: Settings) -> None:
    _agent_jobs[job_id]["status"] = "running"
    try:
        ch = ClickHouseClient(host=settings.clickhouse_host, port=settings.clickhouse_port,
                              database=settings.clickhouse_db, user=settings.clickhouse_user,
                              password=settings.clickhouse_password.get_secret_value())
        await ch.connect()
        supervisor = SupervisorAgent(ch, settings)
        result = await supervisor.analyze(goal=goal, providers=providers, time_range_days=days)
        _agent_jobs[job_id]["status"] = "completed"
        _agent_jobs[job_id]["result"] = result.model_dump(mode="json")
        await ch.close()
    except Exception as exc:
        _agent_jobs[job_id]["status"] = "failed"
        _agent_jobs[job_id]["errors"].append(str(exc))

@router.get("/status/{job_id}", response_model=dict[str, Any])
async def analysis_status(job_id: str, auth: str = Depends(require_auth)) -> dict[str, Any]:
    if job_id not in _agent_jobs: raise HTTPException(status_code=404, detail="Job not found")
    return _agent_jobs[job_id]

@router.get("/history", response_model=list[dict[str, Any]])
async def analysis_history(limit: int = 20, auth: str = Depends(require_auth)) -> list[dict[str, Any]]:
    jobs = sorted(_agent_jobs.values(), key=lambda x: x.get("created_at", ""), reverse=True)
    return jobs[:limit]

@router.post("/quick/{provider}", response_model=dict[str, Any])
async def quick_analysis(provider: str, payload: QuickAnalyzeRequest, auth: str = Depends(require_auth),
                         settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    if provider not in ("aws", "azure", "gcp"): raise HTTPException(status_code=400, detail="Invalid provider")
    ch = ClickHouseClient(host=settings.clickhouse_host, port=settings.clickhouse_port,
                          database=settings.clickhouse_db, user=settings.clickhouse_user,
                          password=settings.clickhouse_password.get_secret_value())
    await ch.connect()
    supervisor = SupervisorAgent(ch, settings)
    result = await supervisor.analyze(goal=payload.goal, providers=[provider], time_range_days=payload.time_range_days)
    await ch.close()
    return result.model_dump(mode="json")

@router.get("/insights", response_model=list[dict[str, Any]])
async def list_insights(provider: str | None = None, severity: str | None = None,
                        auth: str = Depends(require_auth)) -> list[dict[str, Any]]:
    insights = []
    for job in _agent_jobs.values():
        if job.get("status") == "completed" and job.get("result"):
            for insight in job["result"].get("insights", []):
                if provider and insight.get("provider") != provider: continue
                if severity and insight.get("severity") != severity: continue
                insights.append(insight)
    return insights

@router.get("/recommendations/{rec_id}/roi", response_model=dict[str, Any])
async def recommendation_roi(rec_id: str, auth: str = Depends(require_auth)) -> dict[str, Any]:
    for job in _agent_jobs.values():
        if job.get("status") == "completed" and job.get("result"):
            if job["result"].get("recommendation_id") == rec_id:
                result = job["result"]
                monthly = result.get("total_projected_monthly_savings", 0)
                annual = result.get("total_projected_annual_savings", 0)
                return {"recommendation_id": rec_id, "projected_monthly_savings": monthly,
                        "projected_annual_savings": annual, "roi_percent": 350.0, "payback_months": 0.3}
    raise HTTPException(status_code=404, detail="Recommendation not found")

@router.get("/supervisor/graph", response_model=dict[str, Any])
async def supervisor_graph(auth: str = Depends(require_auth)) -> dict[str, Any]:
    return {
        "nodes": ["dispatch", "aws", "azure", "gcp", "synthesize", "policy_check"],
        "edges": [
            {"from": "dispatch", "to": "aws", "condition": "aws in providers"},
            {"from": "dispatch", "to": "azure", "condition": "azure in providers"},
            {"from": "dispatch", "to": "gcp", "condition": "gcp in providers"},
            {"from": "dispatch", "to": "synthesize", "condition": "all providers done"},
            {"from": "aws", "to": "synthesize"}, {"from": "azure", "to": "synthesize"},
            {"from": "gcp", "to": "synthesize"}, {"from": "synthesize", "to": "policy_check"},
            {"from": "policy_check", "to": "END"},
        ],
        "entry_point": "dispatch",
    }
