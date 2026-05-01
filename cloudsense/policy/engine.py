"""OPA Policy Engine — action gating."""
from __future__ import annotations
from typing import Any
import httpx
import structlog
from cloudsense.agents.shared_types import CostInsight
from cloudsense.services.api.config import Settings, get_settings

logger = structlog.get_logger()

class PolicyEngine:
    def __init__(self, opa_url: str | None = None) -> None:
        self._settings = get_settings()
        self._opa_url = opa_url or self._settings.opa_url
        self._fallback_mode = False

    async def evaluate(self, insight: CostInsight) -> dict[str, Any]:
        input_data = {
            "insight_id": insight.insight_id, "action_type": insight.action_type or "unknown",
            "risk_level": insight.risk_level or "low", "provider": insight.provider,
            "service_name": insight.service_name or "", "severity": insight.severity.value,
            "approved": insight.status.value == "resolved"}
        if self._fallback_mode: return self._local_evaluate(input_data)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(f"{self._opa_url}/allow", json={"input": input_data})
                resp.raise_for_status()
                data = resp.json()
                allowed = data.get("result", True)
                return {"allowed": allowed, "reason": None if allowed else "OPA policy denied"}
        except Exception as exc:
            logger.warning("opa_evaluate_failed", error=str(exc), fallback=True)
            self._fallback_mode = True
            return self._local_evaluate(input_data)

    def _local_evaluate(self, input_data: dict[str, Any]) -> dict[str, Any]:
        action = input_data.get("action_type", "")
        risk = input_data.get("risk_level", "low")
        approved = input_data.get("approved", False)
        if action == "delete": return {"allowed": False, "reason": "Delete actions permanently blocked"}
        if action == "stop" and not approved: return {"allowed": False, "reason": "Stop actions require approval"}
        if action == "right-size" and risk == "high" and not approved: return {"allowed": False, "reason": "High-risk right-sizing requires approval"}
        return {"allowed": True, "reason": None}
