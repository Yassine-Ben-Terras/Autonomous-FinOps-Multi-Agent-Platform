"""
Kubernetes Cost API (Phase 5.1) — /api/v1/k8s/*

Kubecost-compatible endpoints:
  GET /k8s/model/allocation           — Kubecost allocation format
  GET /k8s/model/allocation/summary   — Aggregated summary

CloudSense K8s endpoints:
  GET /k8s/namespaces                 — Per-namespace breakdown
  GET /k8s/workloads                  — Per-workload breakdown
  GET /k8s/nodes                      — Per-node cost + efficiency
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from cloudsense.auth.deps import require_permission
from cloudsense.auth.models import Permission, TokenClaims
from cloudsense.k8s.cost_service import K8sCostService
from cloudsense.services.api.config import Settings, get_settings
from cloudsense.services.db.clickhouse import ClickHouseClient

router = APIRouter(prefix="/k8s", tags=["Kubernetes Costs (Phase 5.1)"])


async def _get_service(settings: Settings = Depends(get_settings)) -> K8sCostService:
    ch = ClickHouseClient(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        database=settings.clickhouse_db,
        user=settings.clickhouse_user,
        password=settings.clickhouse_password.get_secret_value(),
    )
    await ch.connect()
    return K8sCostService(ch=ch, settings=settings)


# ── Kubecost-compatible ────────────────────────────────────────────────────────

@router.get("/model/allocation", response_model=dict[str, Any])
async def kubecost_allocation(
    window: str = "7d",
    aggregate: str = "namespace",
    cluster: str | None = None,
    claims: TokenClaims = Depends(require_permission(Permission.K8S_COSTS_READ)),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """
    Kubecost-compatible allocation endpoint.
    Supports window=7d|30d|24h and aggregate=namespace|controller.
    """
    svc = await _get_service(settings)
    try:
        return await svc.kubecost_allocation(window=window, aggregate=aggregate, cluster=cluster)
    finally:
        if svc._ch._client:
            await svc._ch.close()


@router.get("/model/allocation/summary", response_model=dict[str, Any])
async def kubecost_allocation_summary(
    window: str = "30d",
    cluster: str | None = None,
    claims: TokenClaims = Depends(require_permission(Permission.K8S_COSTS_READ)),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Aggregated cluster-wide cost summary for the given window."""
    svc = await _get_service(settings)
    try:
        namespaces = await svc.allocation_by_namespace(cluster=cluster, window_days=30)
        total = sum(n["totalCost"] for n in namespaces)
        return {
            "code": 200,
            "data": {
                "totalCost": total,
                "namespaceCount": len(namespaces),
                "window": window,
                "cluster": cluster or "all",
                "topNamespaces": namespaces[:5],
            },
        }
    finally:
        if svc._ch._client:
            await svc._ch.close()


# ── CloudSense K8s endpoints ───────────────────────────────────────────────────

@router.get("/namespaces", response_model=list[dict[str, Any]])
async def namespace_costs(
    cluster: str | None = None,
    window_days: int = 7,
    claims: TokenClaims = Depends(require_permission(Permission.K8S_COSTS_READ)),
    settings: Settings = Depends(get_settings),
) -> list[dict[str, Any]]:
    """Per-namespace cost breakdown with daily and monthly projections."""
    svc = await _get_service(settings)
    try:
        return await svc.allocation_by_namespace(cluster=cluster, window_days=window_days)
    finally:
        if svc._ch._client:
            await svc._ch.close()


@router.get("/workloads", response_model=list[dict[str, Any]])
async def workload_costs(
    namespace: str | None = None,
    cluster: str | None = None,
    window_days: int = 7,
    claims: TokenClaims = Depends(require_permission(Permission.K8S_COSTS_READ)),
    settings: Settings = Depends(get_settings),
) -> list[dict[str, Any]]:
    """Per-workload cost breakdown with CPU hours and pod count."""
    svc = await _get_service(settings)
    try:
        return await svc.allocation_by_workload(
            namespace=namespace, cluster=cluster, window_days=window_days
        )
    finally:
        if svc._ch._client:
            await svc._ch.close()


@router.get("/nodes", response_model=list[dict[str, Any]])
async def node_costs(
    cluster: str | None = None,
    window_days: int = 7,
    claims: TokenClaims = Depends(require_permission(Permission.K8S_COSTS_READ)),
    settings: Settings = Depends(get_settings),
) -> list[dict[str, Any]]:
    """Per-node cost and efficiency metrics."""
    svc = await _get_service(settings)
    try:
        return await svc.node_cost_breakdown(cluster=cluster, window_days=window_days)
    finally:
        if svc._ch._client:
            await svc._ch.close()
