"""
CloudSense Slack Bot — Recommendation Delivery & Approvals

Sends cost optimization recommendations to Slack channels and handles
interactive approval workflows. Engineers can approve or reject actions
directly from Slack with full audit trail.

Features:
- Scheduled recommendation digests
- Interactive approval buttons
- Rich formatting with cost breakdowns
- Thread-based discussion
- Rollback command support
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from agents.shared_types import RecommendationResult
from services.api.config import get_settings

logger = logging.getLogger(__name__)

# Slack Block Kit helpers for rich formatting


class SlackMessageBuilder:
    """Builds Slack Block Kit messages for recommendations."""

    @staticmethod
    def build_recommendation_blocks(
        recommendation: RecommendationResult,
    ) -> list[dict[str, Any]]:
        """Build Slack blocks for a single recommendation."""
        risk_emoji = {
            "low": "🟢",
            "medium": "🟡",
            "high": "🟠",
            "critical": "🔴",
        }

        return [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"💡 {recommendation.title[:100]}",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Projected Monthly Savings*\n${recommendation.total_projected_monthly_savings:,.2f}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Risk Level*\n{risk_emoji.get(recommendation.risk_level.value, '⚪')} {recommendation.risk_level.value.title()}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Providers*\n{', '.join(p.upper() for p in recommendation.providers_involved)}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Effort*\n{recommendation.implementation_effort.title()}",
                    },
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": recommendation.description[:3000],
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "✅ Approve",
                            "emoji": True,
                        },
                        "style": "primary",
                        "value": json.dumps({
                            "action": "approve",
                            "recommendation_id": recommendation.recommendation_id,
                        }),
                        "action_id": f"approve_{recommendation.recommendation_id[:8]}",
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "❌ Reject",
                            "emoji": True,
                        },
                        "style": "danger",
                        "value": json.dumps({
                            "action": "reject",
                            "recommendation_id": recommendation.recommendation_id,
                        }),
                        "action_id": f"reject_{recommendation.recommendation_id[:8]}",
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "📊 Details",
                            "emoji": True,
                        },
                        "value": json.dumps({
                            "action": "details",
                            "recommendation_id": recommendation.recommendation_id,
                        }),
                        "action_id": f"details_{recommendation.recommendation_id[:8]}",
                    },
                ],
            },
            {"type": "divider"},
        ]

    @staticmethod
    def build_digest_blocks(
        recommendations: list[RecommendationResult],
        period: str = "weekly",
    ) -> list[dict[str, Any]]:
        """Build a digest message with multiple recommendations."""
        total_savings = sum(r.total_projected_monthly_savings for r in recommendations)

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"📊 CloudSense {period.title()} Cost Digest",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Recommendations*\n{len(recommendations)}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Potential Monthly Savings*\n${total_savings:,.2f}",
                    },
                ],
            },
            {"type": "divider"},
        ]

        for rec in recommendations[:5]:  # Top 5
            blocks.extend(SlackMessageBuilder.build_recommendation_blocks(rec))

        if len(recommendations) > 5:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"_... and {len(recommendations) - 5} more recommendations in dashboard_",
                },
            })

        return blocks

    @staticmethod
    def build_approval_confirmation(
        recommendation: RecommendationResult,
        approved_by: str,
    ) -> list[dict[str, Any]]:
        """Build confirmation message after approval."""
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"✅ *Approved by <@{approved_by}>*\n"
                        f"> {recommendation.title}\n"
                        f"> Savings: ${recommendation.total_projected_monthly_savings:,.2f}/month"
                    ),
                },
            },
        ]

    @staticmethod
    def build_rejection_confirmation(
        recommendation: RecommendationResult,
        rejected_by: str,
        reason: str = "",
    ) -> list[dict[str, Any]]:
        """Build confirmation message after rejection."""
        text = (
            f"❌ *Rejected by <@{rejected_by}>*\n"
            f"> {recommendation.title}"
        )
        if reason:
            text += f"\n> Reason: {reason}"

        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": text,
                },
            },
        ]


class SlackBot:
    """CloudSense Slack bot for recommendation delivery and approvals.

    Uses Slack Web API for posting messages and Block Kit for interactivity.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.builder = SlackMessageBuilder()
        self._client: Any = None

    def _get_client(self) -> Any:
        """Lazy-init Slack client."""
        if self._client is None:
            try:
                from slack_sdk.web.async_client import AsyncWebClient
                self._client = AsyncWebClient(token=self.settings.slack_bot_token)
            except ImportError:
                logger.warning("slack-sdk not installed, Slack features disabled")
        return self._client

    async def send_recommendation(
        self,
        recommendation: RecommendationResult,
        channel: str = "#cloudsense-alerts",
    ) -> dict[str, Any] | None:
        """Send a single recommendation to a Slack channel."""
        client = self._get_client()
        if not client:
            return None

        try:
            blocks = self.builder.build_recommendation_blocks(recommendation)
            result = await client.chat_postMessage(
                channel=channel,
                text=f"CloudSense Recommendation: {recommendation.title}",
                blocks=blocks,
            )
            logger.info("Slack message sent: %s", result.get("ts"))
            return {"ok": True, "ts": result.get("ts"), "channel": channel}
        except Exception as exc:
            logger.error("Failed to send Slack message: %s", exc)
            return {"ok": False, "error": str(exc)}

    async def send_digest(
        self,
        recommendations: list[RecommendationResult],
        channel: str = "#cloudsense-alerts",
        period: str = "weekly",
    ) -> dict[str, Any] | None:
        """Send a digest of recommendations."""
        client = self._get_client()
        if not client:
            return None

        try:
            blocks = self.builder.build_digest_blocks(recommendations, period)
            result = await client.chat_postMessage(
                channel=channel,
                text=f"CloudSense {period.title()} Digest — {len(recommendations)} recommendations",
                blocks=blocks,
            )
            logger.info("Digest sent: %s recommendations", len(recommendations))
            return {"ok": True, "ts": result.get("ts"), "channel": channel}
        except Exception as exc:
            logger.error("Failed to send digest: %s", exc)
            return {"ok": False, "error": str(exc)}

    async def send_approval_confirmation(
        self,
        recommendation: RecommendationResult,
        approved_by: str,
        channel: str = "#cloudsense-alerts",
        thread_ts: str | None = None,
    ) -> None:
        """Send approval confirmation (typically in thread)."""
        client = self._get_client()
        if not client:
            return

        try:
            blocks = self.builder.build_approval_confirmation(recommendation, approved_by)
            await client.chat_postMessage(
                channel=channel,
                text=f"Approved: {recommendation.title}",
                blocks=blocks,
                thread_ts=thread_ts,
            )
        except Exception as exc:
            logger.error("Failed to send approval confirmation: %s", exc)

    async def handle_interaction(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Handle Slack interactive button clicks.

        Args:
            payload: Slack action payload

        Returns:
            Response dict
        """
        actions = payload.get("actions", [])
        user = payload.get("user", {}).get("id", "unknown")

        if not actions:
            return {"ok": False, "error": "No actions in payload"}

        action = actions[0]
        try:
            value = json.loads(action.get("value", "{}"))
            action_type = value.get("action")
            rec_id = value.get("recommendation_id")

            if action_type == "approve":
                logger.info("User %s approved recommendation %s", user, rec_id)
                # TODO: Queue for execution via action agent
                return {
                    "ok": True,
                    "action": "approved",
                    "recommendation_id": rec_id,
                    "approved_by": user,
                }

            elif action_type == "reject":
                logger.info("User %s rejected recommendation %s", user, rec_id)
                return {
                    "ok": True,
                    "action": "rejected",
                    "recommendation_id": rec_id,
                    "rejected_by": user,
                }

            elif action_type == "details":
                # TODO: Fetch full details and post in thread
                return {
                    "ok": True,
                    "action": "details_requested",
                    "recommendation_id": rec_id,
                }

            return {"ok": False, "error": f"Unknown action: {action_type}"}

        except json.JSONDecodeError:
            return {"ok": False, "error": "Invalid action value"}

    async def notify_action_execution(
        self,
        action: str,
        resource_id: str,
        status: str,
        channel: str = "#cloudsense-alerts",
    ) -> None:
        """Notify channel about action execution status."""
        client = self._get_client()
        if not client:
            return

        emoji = "✅" if status == "success" else "❌" if status == "failed" else "🔄"
        try:
            await client.chat_postMessage(
                channel=channel,
                text=f"{emoji} Action *{action}* on `{resource_id}`: {status}",
            )
        except Exception as exc:
            logger.error("Failed to send action notification: %s", exc)

    def is_configured(self) -> bool:
        """Check if Slack integration is properly configured."""
        return bool(self.settings.slack_bot_token and self.settings.slack_signing_secret)
