"""Slack Bot for recommendations and approvals."""
from __future__ import annotations
from typing import Any
import httpx
import structlog
from cloudsense.agents.shared_types import RecommendationResult
from cloudsense.services.api.config import Settings, get_settings

logger = structlog.get_logger()

class SlackBot:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._token = self._settings.slack_bot_token
        self._channel = self._settings.slack_channel

    async def send_recommendation(self, rec: RecommendationResult) -> dict[str, Any]:
        if not self._token: return {"status": "skipped", "reason": "Slack token not configured"}
        blocks = self._build_blocks(rec)
        payload = {"channel": self._channel, "text": f"CloudSense: {rec.goal}", "blocks": blocks}
        async with httpx.AsyncClient() as client:
            resp = await client.post("https://slack.com/api/chat.postMessage",
                                     headers={"Authorization": f"Bearer {self._token.get_secret_value()}"}, json=payload)
            resp.raise_for_status()
            data = resp.json()
            logger.info("slack_recommendation_sent", channel=self._channel, ok=data.get("ok"))
            return data

    def _build_blocks(self, rec: RecommendationResult) -> list[dict[str, Any]]:
        insight_texts = []
        for i, insight in enumerate(rec.insights[:5], 1):
            s = insight.projected_monthly_savings or 0
            insight_texts.append(f"{i}. *{insight.title}* — ${s:,.2f}/mo ({insight.confidence_score*100:.0f}% confidence)")
        return [
            {"type": "header", "text": {"type": "plain_text", "text": "CloudSense Recommendation", "emoji": True}},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*Goal:*\n{rec.goal}"},
                {"type": "mrkdwn", "text": f"*Savings:*\n${rec.total_projected_monthly_savings:,.2f}/mo"}]},
            {"type": "section", "text": {"type": "mrkdwn", "text": "*Insights:*\n" + "\n".join(insight_texts)}},
            {"type": "actions", "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "Approve"}, "style": "primary", "value": rec.recommendation_id, "action_id": "approve"},
                {"type": "button", "text": {"type": "plain_text", "text": "Reject"}, "style": "danger", "value": rec.recommendation_id, "action_id": "reject"}]}]

    async def send_alert(self, message: str, severity: str = "warning") -> dict[str, Any]:
        if not self._token: return {"status": "skipped"}
        emoji = {"critical": ":rotating_light:", "high": ":warning:", "warning": ":information_source:"}.get(severity, ":information_source:")
        payload = {"channel": self._channel, "text": f"{emoji} *CloudSense Alert* — {severity.upper()}\n{message}"}
        async with httpx.AsyncClient() as client:
            resp = await client.post("https://slack.com/api/chat.postMessage",
                                     headers={"Authorization": f"Bearer {self._token.get_secret_value()}"}, json=payload)
            resp.raise_for_status()
            return resp.json()
