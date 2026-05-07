"""
CloudSense Phase 5.3 — Audit Log Export API Router
====================================================
Endpoints for exporting the CloudSense audit trail.

GET  /audit/events            Query audit events (paginated)
POST /audit/export            Trigger export to configured destinations
GET  /audit/export/{job_id}   Poll export job status
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from cloudsense.audit.exporter import AuditEvent, AuditExporter
from cloudsense.auth.deps import require_permission
from cloudsense.auth.models import Permission, TokenClaims
from cloudsense.services.api.config import get_settings

logger = structlog.get_logger()
router = APIRouter(prefix="/audit", tags=["Audit Log Export (Phase 5.3)"])

# In-memory event store (production: Postgres audit_log table)
_audit_log: list[AuditEvent] = []
_export_jobs: dict[str, dict[str, Any]] = {}


class ExportRequest(BaseModel):
    destinations: list[str] = ["jsonl"]
    start_time:   datetime | None = None
    end_time:     datetime | None = None
    event_types:  list[str] | None = None
    tenant_slug:  str | None = None


@router.post("/events", status_code=201, summary="Record an audit event (internal)")
async def record_event(
    event: dict[str, Any],
    token: TokenClaims = Depends(require_permission(Permission.ADMIN)),
) -> dict[str, str]:
    """Record a new audit event into the audit trail."""
    evt = AuditEvent(**event)
    _audit_log.append(evt)
    return {"event_id": evt.event_id, "status": "recorded"}


@router.get("/events", summary="Query audit events")
async def query_events(
    limit:       int              = Query(100, le=1000),
    offset:      int              = Query(0),
    event_type:  str | None       = Query(None),
    actor_id:    str | None       = Query(None),
    outcome:     str | None       = Query(None),
    token: TokenClaims            = Depends(require_permission(Permission.ADMIN)),
) -> dict[str, Any]:
    """Return paginated audit events with optional filters."""
    events = list(_audit_log)
    if event_type:
        events = [e for e in events if e.event_type == event_type]
    if actor_id:
        events = [e for e in events if e.actor_id == actor_id]
    if outcome:
        events = [e for e in events if e.outcome == outcome]
    events.sort(key=lambda e: e.timestamp, reverse=True)
    page = events[offset:offset + limit]
    return {
        "data":   [e.to_dict() for e in page],
        "total":  len(events),
        "limit":  limit,
        "offset": offset,
    }


@router.post("/export", summary="Export audit log to external destinations")
async def export_audit_log(
    req:   ExportRequest,
    token: TokenClaims = Depends(require_permission(Permission.ADMIN)),
) -> dict[str, Any]:
    """
    Trigger an async export of audit events to Splunk, Datadog,
    CloudTrail S3, or JSONL. Returns a job_id for polling.
    """
    job_id = str(uuid4())
    _export_jobs[job_id] = {
        "job_id":       job_id,
        "status":       "running",
        "destinations": req.destinations,
        "started_at":   datetime.now(timezone.utc).isoformat(),
        "result":       None,
    }

    # Filter events
    events = list(_audit_log)
    if req.start_time:
        events = [e for e in events if e.timestamp >= req.start_time]
    if req.end_time:
        events = [e for e in events if e.timestamp <= req.end_time]
    if req.event_types:
        events = [e for e in events if e.event_type in req.event_types]
    if req.tenant_slug:
        events = [e for e in events if e.tenant_slug == req.tenant_slug]

    logger.info(
        "audit.export.triggered",
        job_id=job_id,
        events=len(events),
        destinations=req.destinations,
        by=token.sub,
    )

    async def _run() -> None:
        try:
            async with AuditExporter(get_settings()) as exporter:
                result = await exporter.export_all(events, req.destinations)
            _export_jobs[job_id]["status"] = "completed"
            _export_jobs[job_id]["result"] = result
            _export_jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()
        except Exception as exc:
            _export_jobs[job_id]["status"] = "failed"
            _export_jobs[job_id]["error"]  = str(exc)
            logger.error("audit.export.failed", job_id=job_id, error=str(exc))

    import asyncio
    asyncio.create_task(_run())

    return {
        "job_id":       job_id,
        "status":       "running",
        "events_queued": len(events),
        "destinations": req.destinations,
    }


@router.get("/export/{job_id}", summary="Poll audit export job status")
async def get_export_job(
    job_id: str,
    token:  TokenClaims = Depends(require_permission(Permission.ADMIN)),
) -> dict[str, Any]:
    job = _export_jobs.get(job_id)
    if not job:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Export job {job_id} not found")
    return job
