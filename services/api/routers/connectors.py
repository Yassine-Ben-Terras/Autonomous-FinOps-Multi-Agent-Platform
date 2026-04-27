"""CloudSense API — Connector management endpoints."""
from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

class ConnectorInfo(BaseModel):
    id: str
    provider: str
    name: str
    status: str
    last_ingested_at: str | None = None

@router.get("/", summary="List configured cloud connectors")
async def list_connectors() -> dict:
    """Returns all configured cloud connectors and their status."""
    # Phase 1: connector configs are stored in Postgres (connectors table)
    # This is a placeholder — full CRUD in Phase 2
    return {
        "data": [],
        "message": "No connectors configured yet. Use POST /api/v1/connectors to add one.",
    }
