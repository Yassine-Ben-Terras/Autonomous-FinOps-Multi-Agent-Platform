"""Connectors API (/api/v1/connectors/*)."""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from cloudsense.connectors.aws.cost_connector import AWSCostConnector
from cloudsense.connectors.azure.cost_connector import AzureCostConnector
from cloudsense.connectors.gcp.cost_connector import GCPCostConnector
from cloudsense.services.api.config import Settings, get_settings
from cloudsense.services.api.deps import require_auth

router = APIRouter(prefix="/connectors", tags=["Connectors"])
_connectors: dict[str, dict[str, Any]] = {}

class ConnectorConfig(BaseModel):
    provider: str = Field(..., pattern="^(aws|azure|gcp)$")
    connector_id: str = Field(..., min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)

@router.get("", response_model=list[dict[str, Any]])
async def list_connectors(auth: str = Depends(require_auth)) -> list[dict[str, Any]]:
    return list(_connectors.values())

@router.post("/register", response_model=dict[str, Any])
async def register_connector(payload: ConnectorConfig, auth: str = Depends(require_auth)) -> dict[str, Any]:
    key = f"{payload.provider}:{payload.connector_id}"
    _connectors[key] = {"provider": payload.provider, "connector_id": payload.connector_id,
                        "config": payload.config, "registered_at": "2024-01-01T00:00:00Z"}
    return {"message": "Connector registered", "connector": _connectors[key]}

@router.get("/health", response_model=dict[str, Any])
async def connectors_health(auth: str = Depends(require_auth), settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    results = []
    for key, conn in _connectors.items():
        provider, cid = conn["provider"], conn["connector_id"]
        cfg = conn["config"]
        try:
            if provider == "aws": connector = AWSCostConnector(cid, cfg)
            elif provider == "azure": connector = AzureCostConnector(cid, cfg)
            elif provider == "gcp": connector = GCPCostConnector(cid, cfg)
            else: continue
            health = await connector.health_check()
            results.append(health)
            await connector.close()
        except Exception as exc:
            results.append({"status": "error", "provider": provider, "connector_id": cid, "error": str(exc)})
    healthy = sum(1 for r in results if r.get("status") == "healthy")
    return {"total": len(results), "healthy": healthy, "unhealthy": len(results) - healthy, "details": results}

@router.post("/{provider}/test", response_model=dict[str, Any])
async def test_connector(provider: str, payload: ConnectorConfig, auth: str = Depends(require_auth)) -> dict[str, Any]:
    try:
        if provider == "aws": connector = AWSCostConnector(payload.connector_id, payload.config)
        elif provider == "azure": connector = AzureCostConnector(payload.connector_id, payload.config)
        elif provider == "gcp": connector = GCPCostConnector(payload.connector_id, payload.config)
        else: raise HTTPException(status_code=400, detail="Invalid provider")
        health = await connector.health_check()
        await connector.close()
        return health
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
