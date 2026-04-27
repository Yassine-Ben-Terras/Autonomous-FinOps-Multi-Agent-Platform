"""
CloudSense API — Ingestion Trigger Endpoints
Manually trigger a billing data pull for a specific connector.
In production, ingestion runs on a schedule via Celery beat.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class IngestionTriggerRequest(BaseModel):
    provider: str                      # "aws" | "azure" | "gcp"
    connector_id: str                  # ID of the configured connector
    start_date: date | None = None
    end_date: date | None = None


class IngestionTriggerResponse(BaseModel):
    task_id: str
    status: str
    message: str
    provider: str
    period: dict[str, str]


@router.post("/trigger", summary="Trigger billing data ingestion")
async def trigger_ingestion(
    request: IngestionTriggerRequest,
) -> IngestionTriggerResponse:
    """
    Enqueues a billing ingestion task for the specified cloud connector.
    The task runs asynchronously via Celery and streams results to ClickHouse via Kafka.
    """
    if request.provider not in ("aws", "azure", "gcp"):
        raise HTTPException(status_code=400, detail=f"Unknown provider: {request.provider}")

    start = request.start_date or (date.today() - timedelta(days=30))
    end   = request.end_date   or date.today()

    if start >= end:
        raise HTTPException(status_code=400, detail="start_date must be before end_date")

    # In Phase 1, we import the Celery task directly
    # In production this goes through Celery's apply_async
    try:
        from services.ingestion.tasks import run_ingestion_task
        task = run_ingestion_task.apply_async(
            kwargs={
                "provider":      request.provider,
                "connector_id":  request.connector_id,
                "start_date":    str(start),
                "end_date":      str(end),
            },
            queue="billing_ingestion",
        )
        task_id = task.id
    except ImportError:
        # Celery not available in test/dev — return a mock task ID
        import uuid
        task_id = str(uuid.uuid4())

    return IngestionTriggerResponse(
        task_id=task_id,
        status="queued",
        message=f"Ingestion task queued for {request.provider} connector '{request.connector_id}'",
        provider=request.provider,
        period={"start": str(start), "end": str(end)},
    )


@router.get("/status/{task_id}", summary="Get ingestion task status")
async def get_ingestion_status(task_id: str) -> dict[str, Any]:
    """Check the status of a previously triggered ingestion task."""
    try:
        from celery.result import AsyncResult
        result = AsyncResult(task_id)
        return {
            "task_id": task_id,
            "status":  result.status,
            "result":  result.result if result.ready() else None,
        }
    except ImportError:
        return {"task_id": task_id, "status": "UNKNOWN", "result": None}
