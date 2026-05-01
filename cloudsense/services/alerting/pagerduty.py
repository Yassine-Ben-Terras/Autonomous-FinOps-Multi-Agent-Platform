"""PagerDuty integration."""
from __future__ import annotations
import httpx
import structlog

logger = structlog.get_logger()

class PagerDutyClient:
    def __init__(self, service_key: str, api_base: str = "https://events.pagerduty.com/v2/enqueue") -> None:
        self.service_key = service_key
        self.api_base = api_base

    async def trigger_incident(self, summary: str, severity: str = "critical", source: str = "cloudsense",
                               custom_details: dict | None = None) -> dict:
        payload = {
            "routing_key": self.service_key, "event_action": "trigger",
            "dedup_key": f"cloudsense:{source}:{summary[:50]}",
            "payload": {"summary": summary, "severity": severity, "source": source,
                        "custom_details": custom_details or {}}}
        async with httpx.AsyncClient() as client:
            resp = await client.post(self.api_base, json=payload)
            resp.raise_for_status()
            data = resp.json()
            logger.info("pagerduty_triggered", dedup_key=payload["dedup_key"])
            return data

    async def resolve_incident(self, dedup_key: str) -> dict:
        payload = {"routing_key": self.service_key, "event_action": "resolve", "dedup_key": dedup_key}
        async with httpx.AsyncClient() as client:
            resp = await client.post(self.api_base, json=payload)
            resp.raise_for_status()
            return resp.json()
