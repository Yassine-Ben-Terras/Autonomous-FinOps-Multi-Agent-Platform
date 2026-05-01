"""OpsGenie integration."""
from __future__ import annotations
import httpx
import structlog

logger = structlog.get_logger()

class OpsGenieClient:
    def __init__(self, api_key: str, api_base: str = "https://api.opsgenie.com/v2/alerts") -> None:
        self.api_key = api_key
        self.api_base = api_base

    async def create_alert(self, message: str, priority: str = "P2", alias: str | None = None,
                           description: str | None = None, tags: list[str] | None = None) -> dict:
        headers = {"Authorization": f"GenieKey {self.api_key}"}
        payload = {"message": message, "priority": priority, "alias": alias or message[:50],
                   "description": description, "tags": tags or ["finops", "cloudsense"]}
        async with httpx.AsyncClient() as client:
            resp = await client.post(self.api_base, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            logger.info("opsgenie_alert_created", alias=payload["alias"])
            return data
