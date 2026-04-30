"""
Tagging API (Phase 4) — /api/v1/tags/*

Endpoints:
  GET    /tags/compliance           — Full compliance report
  GET    /tags/violations           — List tag violations
  POST   /tags/infer                — Infer tags for a resource via LLM
  POST   /tags/apply                — Apply tags to a cloud resource (OPA-gated)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from cloudsense.agents.specialist.tagging_agent import TaggingAgent
from cloudsense.core.models.enums import CloudProvider
from cloudsense.services.api.config import Settings, get_settings
from cloudsense.services.api.deps import require_auth
from cloudsense.services.db.clickhouse import ClickHouseClient

router = APIRouter(prefix="/tags", tags=["Tags (Phase 4)"])


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _get_agent(settings: Settings = Depends(get_settings)) -> TaggingAgent:
    ch = ClickHouseClient(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        database=settings.clickhouse_db,
        user=settings.clickhouse_user,
        password=settings.clickhouse_password.get_secret_value(),
    )
    await ch.connect()
    return TaggingAgent(clickhouse_client=ch, settings=settings)


# ── Request models ───────────────────────────────────────────────────────────

class InferTagsRequest(BaseModel):
    resource_id: str = Field(...)
    resource_type: str = Field(...)
    billing_account_id: str = Field(...)


class ApplyTagsRequest(BaseModel):
    provider: str = Field(...)
    resource_id: str = Field(...)
    tags: dict[str, str] = Field(...)
    region: str = Field(default="us-east-1")


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/compliance", response_model=dict[str, Any])
async def tag_compliance_report(
    days: int = 30,
    auth: str = Depends(require_auth),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Generate a full tag compliance report (violations, cost at risk, severity breakdown)."""
    agent = await _get_agent(settings)
    try:
        return await agent.compliance_report(time_range_days=days)
    finally:
        if agent._ch._client:
            await agent._ch.close()


@router.get("/violations", response_model=list[dict[str, Any]])
async def list_violations(
    days: int = 30,
    provider: str | None = None,
    auth: str = Depends(require_auth),
    settings: Settings = Depends(get_settings),
) -> list[dict[str, Any]]:
    """List all tag violations with severity and cost at risk."""
    agent = await _get_agent(settings)
    try:
        violations = await agent.scan_violations(time_range_days=days)
        result = [v.model_dump(mode="json") for v in violations]
        if provider:
            result = [v for v in result if v.get("provider") == provider]
        return result
    finally:
        if agent._ch._client:
            await agent._ch.close()


@router.post("/infer", response_model=dict[str, Any])
async def infer_tags(
    body: InferTagsRequest,
    auth: str = Depends(require_auth),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Use LLM to infer missing tags for a resource."""
    agent = await _get_agent(settings)
    try:
        inferred = await agent.infer_tags(
            resource_id=body.resource_id,
            resource_type=body.resource_type,
            billing_account_id=body.billing_account_id,
        )
        return {
            "resource_id": body.resource_id,
            "inferred_tags": inferred,
            "model": settings.llm_default_model,
        }
    finally:
        if agent._ch._client:
            await agent._ch.close()


@router.post("/apply", response_model=dict[str, Any])
async def apply_tags(
    body: ApplyTagsRequest,
    auth: str = Depends(require_auth),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """
    Apply tags to a cloud resource (OPA-gated write operation).
    Non-production resources auto-approve; production requires approval.
    """
    try:
        provider = CloudProvider(body.provider.lower())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {body.provider}")

    agent = await _get_agent(settings)
    try:
        result = await agent.apply_tags(
            provider=provider,
            resource_id=body.resource_id,
            tags=body.tags,
            region=body.region,
        )
        return result
    finally:
        if agent._ch._client:
            await agent._ch.close()
