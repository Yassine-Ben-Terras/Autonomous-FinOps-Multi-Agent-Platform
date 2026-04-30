"""
Actions API (Phase 4) — /api/v1/actions/*

Endpoints:
  POST   /actions/request          — Submit an action for approval
  GET    /actions/pending           — List pending approvals
  POST   /actions/{id}/approve     — Human approves an action
  POST   /actions/{id}/reject      — Human rejects an action
  POST   /actions/{id}/execute     — Execute an approved action
  POST   /actions/{id}/rollback    — Roll back a completed action
  GET    /actions/{id}             — Get action details
  GET    /actions/{id}/audit       — Get audit trail for an action
  GET    /actions/audit/recent     — Recent audit events
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from cloudsense.agents.specialist.action_agent import ActionAgent, RollbackRegistry
from cloudsense.core.models.billing import ActionRequest
from cloudsense.core.models.enums import ActionStatus, CloudProvider, Environment
from cloudsense.services.api.config import Settings, get_settings
from cloudsense.services.api.deps import require_auth
from cloudsense.services.db.postgres import ActionLogRepository

router = APIRouter(prefix="/actions", tags=["Actions (Phase 4)"])

# ── Request / Response models ────────────────────────────────────────────────

class ActionRequestBody(BaseModel):
    recommendation_id: str = Field(..., description="ID of the parent recommendation")
    provider: str = Field(..., description="aws | azure | gcp")
    environment: str = Field(..., description="development | staging | production")
    action_type: str = Field(..., description="stop_instance | rightsize | purchase_ri | ...")
    target_resource_id: str = Field(..., description="Cloud resource ID to act on")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Action-specific params")
    rollback_plan: dict[str, Any] = Field(default_factory=dict)
    requested_by: str = Field(default="api-user")


class ApproveBody(BaseModel):
    approved_by: str = Field(..., description="Username of approver")


class RejectBody(BaseModel):
    rejected_by: str = Field(default="api-user")
    reason: str = Field(default="Rejected via API")


# ── Dependencies ─────────────────────────────────────────────────────────────

def _get_repo(settings: Settings = Depends(get_settings)) -> ActionLogRepository:
    return ActionLogRepository(dsn=settings.postgres_dsn)


async def _get_agent(settings: Settings = Depends(get_settings)) -> ActionAgent:
    return ActionAgent(settings=settings)


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/request", response_model=dict[str, Any], status_code=status.HTTP_202_ACCEPTED)
async def request_action(
    body: ActionRequestBody,
    auth: str = Depends(require_auth),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """
    Submit an action request. Non-production actions are auto-queued;
    production actions require explicit human approval.
    """
    action_id = str(uuid4())

    try:
        provider = CloudProvider(body.provider.lower())
        environment = Environment(body.environment.lower())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    action = ActionRequest(
        id=action_id,  # type: ignore[arg-type]
        recommendation_id=body.recommendation_id,  # type: ignore[arg-type]
        provider=provider,
        environment=environment,
        action_type=body.action_type,
        target_resource_id=body.target_resource_id,
        parameters=body.parameters,
        rollback_plan=body.rollback_plan,
        requested_by=body.requested_by,
    )

    repo = ActionLogRepository(dsn=settings.postgres_dsn)
    await repo.connect()
    try:
        await repo.create_approval_request(
            action_id=str(action.id),
            recommendation_id=str(action.recommendation_id),
            provider=provider.value,
            environment=environment.value,
            action_type=body.action_type,
            target_resource_id=body.target_resource_id,
            parameters=body.parameters,
            rollback_plan=body.rollback_plan,
            requested_by=body.requested_by,
        )
    except Exception as exc:
        # DB not available → return action_id anyway (graceful degradation)
        pass
    finally:
        await repo.close()

    requires_approval = environment == Environment.PRODUCTION
    return {
        "action_id": str(action.id),
        "status": ActionStatus.AWAITING_APPROVAL.value if requires_approval else ActionStatus.PENDING.value,
        "requires_human_approval": requires_approval,
        "message": (
            "Action queued for human approval (production environment)."
            if requires_approval
            else "Action queued. Call /execute to run."
        ),
    }


@router.get("/pending", response_model=list[dict[str, Any]])
async def list_pending_actions(
    environment: str | None = None,
    auth: str = Depends(require_auth),
    settings: Settings = Depends(get_settings),
) -> list[dict[str, Any]]:
    """List all pending action approvals (optionally filtered by environment)."""
    repo = ActionLogRepository(dsn=settings.postgres_dsn)
    await repo.connect()
    try:
        return await repo.list_pending_actions(environment=environment)
    except Exception:
        return []
    finally:
        await repo.close()


@router.post("/{action_id}/approve", response_model=dict[str, Any])
async def approve_action(
    action_id: str,
    body: ApproveBody,
    auth: str = Depends(require_auth),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Human approves a pending action."""
    repo = ActionLogRepository(dsn=settings.postgres_dsn)
    await repo.connect()
    try:
        ok = await repo.approve_action(action_id=action_id, approved_by=body.approved_by)
        if not ok:
            raise HTTPException(status_code=404, detail="Action not found or not in pending state")
        return {
            "action_id": action_id,
            "status": ActionStatus.APPROVED.value,
            "approved_by": body.approved_by,
            "message": "Action approved. Call /execute to run.",
        }
    finally:
        await repo.close()


@router.post("/{action_id}/reject", response_model=dict[str, Any])
async def reject_action(
    action_id: str,
    body: RejectBody,
    auth: str = Depends(require_auth),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Human rejects a pending action."""
    repo = ActionLogRepository(dsn=settings.postgres_dsn)
    await repo.connect()
    try:
        ok = await repo.reject_action(
            action_id=action_id, rejected_by=body.rejected_by, reason=body.reason
        )
        if not ok:
            raise HTTPException(status_code=404, detail="Action not found or not in pending state")
        return {
            "action_id": action_id,
            "status": ActionStatus.REJECTED.value,
            "reason": body.reason,
        }
    finally:
        await repo.close()


@router.post("/{action_id}/execute", response_model=dict[str, Any])
async def execute_action(
    action_id: str,
    auth: str = Depends(require_auth),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """
    Execute a previously approved action.
    Builds rollback plan, calls provider API, logs result.
    """
    repo = ActionLogRepository(dsn=settings.postgres_dsn)
    await repo.connect()
    try:
        action_data = await repo.get_action(action_id)
    finally:
        await repo.close()

    if not action_data:
        raise HTTPException(status_code=404, detail="Action not found")

    if action_data["status"] not in ("approved", "pending"):
        raise HTTPException(
            status_code=409,
            detail=f"Action is in '{action_data['status']}' state — cannot execute",
        )

    if action_data["environment"] == "production" and action_data["status"] != "approved":
        raise HTTPException(
            status_code=403,
            detail="Production actions require explicit approval before execution",
        )

    try:
        provider = CloudProvider(action_data["provider"].lower())
        environment = Environment(action_data["environment"].lower())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    action_request = ActionRequest(
        id=action_id,  # type: ignore[arg-type]
        recommendation_id=action_data.get("recommendation_id", str(uuid4())),  # type: ignore[arg-type]
        provider=provider,
        environment=environment,
        action_type=action_data["action_type"],
        target_resource_id=action_data["target_resource_id"],
        parameters=action_data.get("parameters", {}),
        rollback_plan=action_data.get("rollback_plan", {}),
        requested_by=action_data.get("requested_by", "system"),
        approved_by=action_data.get("approved_by"),
    )

    # Build rollback registry backed by postgres
    repo2 = ActionLogRepository(dsn=settings.postgres_dsn)
    await repo2.connect()
    registry = RollbackRegistry(repo=repo2)

    agent = ActionAgent(settings=settings, rollback_registry=registry)
    result = await agent.execute(
        action_request=action_request,
        approved_by=action_data.get("approved_by"),
    )
    await repo2.close()
    return result


@router.post("/{action_id}/rollback", response_model=dict[str, Any])
async def rollback_action(
    action_id: str,
    auth: str = Depends(require_auth),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """
    Roll back a completed action within the 7-day rollback window.
    """
    repo = ActionLogRepository(dsn=settings.postgres_dsn)
    await repo.connect()
    registry = RollbackRegistry(repo=repo)
    agent = ActionAgent(settings=settings, rollback_registry=registry)
    result = await agent.rollback(action_id=action_id)
    await repo.close()
    return result


@router.get("/{action_id}", response_model=dict[str, Any])
async def get_action(
    action_id: str,
    auth: str = Depends(require_auth),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Get full details of an action (status, rollback plan, audit trail)."""
    repo = ActionLogRepository(dsn=settings.postgres_dsn)
    await repo.connect()
    try:
        action = await repo.get_action(action_id)
        if not action:
            raise HTTPException(status_code=404, detail="Action not found")
        audit = await repo.list_audit_events(resource_id=action_id, limit=50)
        return {**action, "audit_trail": audit}
    finally:
        await repo.close()


@router.get("/{action_id}/audit", response_model=list[dict[str, Any]])
async def get_action_audit(
    action_id: str,
    auth: str = Depends(require_auth),
    settings: Settings = Depends(get_settings),
) -> list[dict[str, Any]]:
    """Full audit trail for a specific action (append-only events)."""
    repo = ActionLogRepository(dsn=settings.postgres_dsn)
    await repo.connect()
    try:
        return await repo.list_audit_events(resource_id=action_id)
    finally:
        await repo.close()


@router.get("/audit/recent", response_model=list[dict[str, Any]])
async def recent_audit_events(
    limit: int = 50,
    auth: str = Depends(require_auth),
    settings: Settings = Depends(get_settings),
) -> list[dict[str, Any]]:
    """Most recent audit log events across all actions."""
    repo = ActionLogRepository(dsn=settings.postgres_dsn)
    await repo.connect()
    try:
        return await repo.list_audit_events(limit=limit)
    finally:
        await repo.close()
