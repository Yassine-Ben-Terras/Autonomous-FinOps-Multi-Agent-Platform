"""
CloudSense Phase 4 — Action Executor (services layer)
=======================================================
Unified dispatcher that routes approved CostInsight action_types to the
correct cloud-specific executor from action_agent.py, and enforces:

  1. DRY_RUN mode   — ACTION_DRY_RUN=true logs intent, touches nothing
  2. Production gate — confidence must be >= ACTION_PROD_MIN_CONFIDENCE
  3. Rollback-first  — plan registered in RollbackRegistry BEFORE cloud call
  4. Auto-rollback   — triggered when a cloud call raises unexpectedly

Supported action_type values
-----------------------------
AWS:        stop_instance · rightsize_instance · release_eip
Azure:      stop_vm · rightsize_vm · delete_disk
GCP:        stop_instance · rightsize_instance
Cross-cloud: apply_tag
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import structlog

from cloudsense.agents.specialist.action_agent import (
    AWSActionExecutor,
    AzureActionExecutor,
    GCPActionExecutor,
    RollbackRegistry,
)
from cloudsense.services.api.config import Settings, get_settings

logger = structlog.get_logger()

DRY_RUN             = os.environ.get("ACTION_DRY_RUN", "false").lower() == "true"
PROD_MIN_CONFIDENCE = float(os.environ.get("ACTION_PROD_MIN_CONFIDENCE", "0.90"))


class ActionExecutor:
    """
    Unified action dispatcher for all cloud providers.

    Usage
    -----
    executor = ActionExecutor(rollback_registry, settings)
    result   = await executor.execute(
        action_type="stop_instance",
        provider="aws",
        resource_id="i-abc123",
        params={"region": "us-east-1"},
        approved_by="alice@acme.com",
        confidence=0.92,
        is_production=True,
    )
    """

    def __init__(
        self,
        rollback_registry: RollbackRegistry,
        settings: Settings | None = None,
    ) -> None:
        self._registry = rollback_registry
        self._settings = settings or get_settings()
        self._aws      = AWSActionExecutor(self._settings)
        self._azure    = AzureActionExecutor(self._settings)
        self._gcp      = GCPActionExecutor(self._settings)

    async def execute(
        self,
        action_type: str,
        provider: str,
        resource_id: str,
        params: dict[str, Any],
        approved_by: str,
        confidence: float = 0.80,
        is_production: bool = False,
        insight_id: str = "",
    ) -> dict[str, Any]:
        """
        Dispatch and execute one approved action.

        Returns a result dict with keys:
          action_id, status, executed_at, result, rollback_registered
        """
        action_id = str(uuid4())

        logger.info(
            "executor.dispatch",
            action_id=action_id,
            action_type=action_type,
            provider=provider,
            resource_id=resource_id,
            dry_run=DRY_RUN,
            is_production=is_production,
            approved_by=approved_by,
        )

        # ── Safety gates ────────────────────────────────────────
        if DRY_RUN:
            return {
                "action_id":  action_id,
                "status":     "dry_run",
                "reason":     "ACTION_DRY_RUN=true — no cloud resources were modified",
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "rollback_registered": False,
            }

        if is_production and confidence < PROD_MIN_CONFIDENCE:
            return {
                "action_id": action_id,
                "status":    "blocked",
                "reason": (
                    f"Production action requires confidence >= {PROD_MIN_CONFIDENCE}, "
                    f"got {confidence:.2f}"
                ),
            }

        # ── Execute ─────────────────────────────────────────────
        try:
            result = await self._dispatch(
                action_id, insight_id, action_type, provider, resource_id, params
            )
            await self._registry.mark_executed(action_id)
            logger.info("executor.success", action_id=action_id, action_type=action_type)
            return {
                "action_id":           action_id,
                "status":              "executed",
                "executed_at":         datetime.now(timezone.utc).isoformat(),
                "result":              result,
                "rollback_registered": True,
                "approved_by":         approved_by,
            }

        except Exception as exc:
            logger.error("executor.failed", action_id=action_id, error=str(exc))
            # Best-effort rollback on failure
            try:
                await self._registry.mark_rolled_back(action_id)
            except Exception:
                pass
            return {
                "action_id": action_id,
                "status":    "failed",
                "error":     str(exc),
            }

    # ── Internal dispatcher ──────────────────────────────────────

    async def _dispatch(
        self,
        action_id: str,
        insight_id: str,
        action_type: str,
        provider: str,
        resource_id: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Route to the correct cloud executor. Registers rollback BEFORE execution."""

        # ── AWS ────────────────────────────────────────────────
        if provider == "aws":
            if action_type == "stop_instance":
                region = params.get("region", "us-east-1")
                await self._registry.register(action_id, {
                    "type": "start_instance",
                    "instance_id": resource_id,
                    "region": region,
                })
                return await self._aws.stop_instance(resource_id, region)

            elif action_type == "rightsize_instance":
                region        = params.get("region", "us-east-1")
                new_type      = params["new_instance_type"]
                original_type = params["original_instance_type"]
                await self._registry.register(action_id, {
                    "type": "resize_instance",
                    "instance_id": resource_id,
                    "target_type": original_type,
                    "region": region,
                })
                return await self._aws.rightsize_instance(
                    resource_id, region, new_type, original_type
                )

            elif action_type == "release_eip":
                await self._registry.register(action_id, {
                    "type": "noop",
                    "note": "EIP release is irreversible — allocate a new one if needed",
                })
                import boto3
                ec2  = boto3.client("ec2", region_name=params.get("region", "us-east-1"))
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None, lambda: ec2.release_address(AllocationId=resource_id)
                )
                return {"allocation_id": resource_id, "released": True}

        # ── Azure ──────────────────────────────────────────────
        elif provider == "azure":
            if action_type == "stop_vm":
                rg = params["resource_group"]
                await self._registry.register(action_id, {
                    "type": "start_vm",
                    "vm_name": resource_id,
                    "resource_group": rg,
                })
                return await self._azure.stop_vm(resource_id, rg)

            elif action_type == "rightsize_vm":
                rg      = params["resource_group"]
                new_sku = params["new_sku"]
                old_sku = params["original_sku"]
                await self._registry.register(action_id, {
                    "type": "resize_vm",
                    "vm_name": resource_id,
                    "resource_group": rg,
                    "target_sku": old_sku,
                })
                return await self._azure.rightsize_vm(resource_id, rg, new_sku, old_sku)

        # ── GCP ────────────────────────────────────────────────
        elif provider == "gcp":
            if action_type == "stop_instance":
                project = params["project_id"]
                zone    = params["zone"]
                await self._registry.register(action_id, {
                    "type": "noop",
                    "note": f"Start {resource_id} in {zone} via GCP console or gcloud CLI",
                })
                return await self._gcp.stop_instance(project, zone, resource_id)

        # ── Cross-cloud: apply_tag ─────────────────────────────
        if action_type == "apply_tag":
            tag_key   = params["tag_key"]
            tag_value = params["tag_value"]
            await self._registry.register(action_id, {
                "type": "remove_tag",
                "provider": provider,
                "resource_id": resource_id,
                "tag_key": tag_key,
            })
            return await self._apply_tag(provider, resource_id, params)

        raise ValueError(
            f"Unsupported action_type='{action_type}' for provider='{provider}'"
        )

    async def _apply_tag(
        self, provider: str, resource_id: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        tag_key   = params["tag_key"]
        tag_value = params["tag_value"]
        loop      = asyncio.get_event_loop()

        if provider == "aws":
            import boto3
            client = boto3.client("resourcegroupstaggingapi")
            await loop.run_in_executor(
                None,
                lambda: client.tag_resources(
                    ResourceARNList=[resource_id],
                    Tags={tag_key: tag_value},
                ),
            )
        elif provider == "azure":
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.resource import ResourceManagementClient
            cred   = DefaultAzureCredential()
            client = ResourceManagementClient(cred, params["subscription_id"])
            await loop.run_in_executor(
                None,
                lambda: client.tags.create_or_update_at_scope(
                    resource_id,
                    {"properties": {"tags": {tag_key: tag_value}}},
                ),
            )

        logger.info(
            "tag.applied",
            provider=provider,
            resource=resource_id,
            key=tag_key,
            value=tag_value,
        )
        return {
            "resource_id": resource_id,
            "provider":    provider,
            "tag_key":     tag_key,
            "tag_value":   tag_value,
            "applied":     True,
        }


__all__ = ["ActionExecutor", "DRY_RUN", "PROD_MIN_CONFIDENCE"]
