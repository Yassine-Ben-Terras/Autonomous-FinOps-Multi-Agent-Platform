"""Ingestion API (/api/v1/ingestion/*)."""
from __future__ import annotations
from datetime import date, timedelta
from typing import Any
from uuid import uuid4
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from cloudsense.connectors.aws.cost_connector import AWSCostConnector
from cloudsense.connectors.azure.cost_connector import AzureCostConnector
from cloudsense.connectors.gcp.cost_connector import GCPCostConnector
from cloudsense.services.api.config import Settings, get_settings
from cloudsense.services.api.deps import require_auth
from cloudsense.services.db.clickhouse import ClickHouseClient

router = APIRouter(prefix="/ingestion", tags=["Ingestion"])
_ingestion_jobs: dict[str, dict[str, Any]] = {}

class IngestionTrigger(BaseModel):
    provider: str = Field(..., pattern="^(aws|azure|gcp)$")
    connector_id: str = Field(..., min_length=1)
    start_date: date | None = None
    end_date: date | None = None
    config: dict[str, Any] = Field(default_factory=dict)

@router.post("/trigger", response_model=dict[str, Any])
async def trigger_ingestion(payload: IngestionTrigger, background_tasks: BackgroundTasks,
                            auth: str = Depends(require_auth), settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    job_id = str(uuid4())
    end = payload.end_date or date.today()
    start = payload.start_date or (end - timedelta(days=30))
    _ingestion_jobs[job_id] = {"id": job_id, "status": "queued", "provider": payload.provider,
                               "connector_id": payload.connector_id, "start_date": start.isoformat(),
                               "end_date": end.isoformat(), "records_processed": 0, "errors": []}
    background_tasks.add_task(_run_ingestion, job_id=job_id, provider=payload.provider,
                              connector_id=payload.connector_id, start=start, end=end,
                              config=payload.config, settings=settings)
    return {"job_id": job_id, "status": "queued", "message": "Ingestion job started"}

async def _run_ingestion(job_id: str, provider: str, connector_id: str, start: date, end: date,
                         config: dict[str, Any], settings: Settings) -> None:
    _ingestion_jobs[job_id]["status"] = "running"
    try:
        if provider == "aws": connector = AWSCostConnector(connector_id, config)
        elif provider == "azure": connector = AzureCostConnector(connector_id, config)
        elif provider == "gcp": connector = GCPCostConnector(connector_id, config)
        else: raise ValueError(f"Unknown provider: {provider}")
        ch = ClickHouseClient(host=settings.clickhouse_host, port=settings.clickhouse_port,
                              database=settings.clickhouse_db, user=settings.clickhouse_user,
                              password=settings.clickhouse_password.get_secret_value())
        await ch.connect(); await ch.init_schema()
        total_records = 0
        async for batch in connector.fetch_billing(start, end):
            count = await ch.insert_focus_records(batch.records)
            total_records += count
            _ingestion_jobs[job_id]["records_processed"] = total_records
        _ingestion_jobs[job_id]["status"] = "completed"
        _ingestion_jobs[job_id]["records_processed"] = total_records
        await ch.close(); await connector.close()
    except Exception as exc:
        _ingestion_jobs[job_id]["status"] = "failed"
        _ingestion_jobs[job_id]["errors"].append(str(exc))

@router.get("/status/{job_id}", response_model=dict[str, Any])
async def ingestion_status(job_id: str, auth: str = Depends(require_auth)) -> dict[str, Any]:
    if job_id not in _ingestion_jobs: raise HTTPException(status_code=404, detail="Job not found")
    return _ingestion_jobs[job_id]
