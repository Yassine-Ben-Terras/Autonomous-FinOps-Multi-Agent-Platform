"""
Tagging Agent (Phase 4) — Tag compliance scanner and auto-tagger.

Responsibilities:
  1. Scan all cloud resources for missing or non-compliant tags.
  2. Infer correct tags from resource names / account structure using
     LLM classification (Claude Sonnet via langchain-anthropic).
  3. Enforce custom OPA-based tagging policies.
  4. Produce TagViolation records and CostInsights for the supervisor.
  5. Optionally apply inferred tags (write-level, OPA-gated).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

import structlog
from langchain_anthropic import ChatAnthropic
from langchain.schema import HumanMessage, SystemMessage

from cloudsense.agents.shared_types import CostInsight, InsightSeverity, InsightStatus
from cloudsense.core.models.billing import TagViolation
from cloudsense.core.models.enums import CloudProvider
from cloudsense.policy.engine import PolicyEngine
from cloudsense.services.api.config import Settings, get_settings
from cloudsense.services.db.clickhouse import ClickHouseClient

logger = structlog.get_logger()

# ── Required tags by default policy ────────────────────────────
REQUIRED_TAGS = ["team", "environment", "project", "owner"]

# ── System prompt for LLM tag inference ────────────────────────
TAG_INFERENCE_SYSTEM = """You are a FinOps tag inference engine.
Given a cloud resource's name, type, and billing account, infer the most likely
values for these required tags: team, environment, project, owner.

Return ONLY a JSON object with those 4 keys. No explanation, no markdown.
Use lowercase values. Use "unknown" if you cannot infer a value.
Example output:
{"team":"platform","environment":"production","project":"api-gateway","owner":"infra-team"}"""


class TaggingAgent:
    """
    Phase 4 Tagging Agent.

    Scan → Infer → (optionally) Apply
    """

    def __init__(
        self,
        clickhouse_client: ClickHouseClient,
        settings: Settings | None = None,
    ) -> None:
        self._ch = clickhouse_client
        self._settings = settings or get_settings()
        self._policy = PolicyEngine()
        self._llm = ChatAnthropic(
            model=self._settings.llm_default_model,
            anthropic_api_key=(
                self._settings.anthropic_api_key.get_secret_value()
                if self._settings.anthropic_api_key else None
            ),
            temperature=0.0,
            max_tokens=256,
        )

    # ── Public API ──────────────────────────────────────────────

    async def analyze(self, time_range_days: int = 30) -> list[CostInsight]:
        """
        Run full compliance scan. Returns CostInsight list for the supervisor.
        """
        logger.info("tagging_agent_start", days=time_range_days)
        violations = await self.scan_violations(time_range_days)
        insights: list[CostInsight] = []

        for v in violations:
            # Infer tags for resources that have NO tags at all
            inferred: dict[str, str] = {}
            if not v.non_compliant_tags and len(v.missing_tags) == len(REQUIRED_TAGS):
                inferred = await self.infer_tags(
                    resource_id=v.resource_id,
                    resource_type=v.resource_type or "unknown",
                    billing_account_id=v.billing_account_id,
                )
                v = TagViolation(
                    **{**v.model_dump(), "inferred_tags": inferred}
                )

            severity = _violation_severity(v)
            missing_str = ", ".join(v.missing_tags) if v.missing_tags else "none"
            savings_at_risk = v.monthly_cost_at_risk

            insights.append(CostInsight(
                insight_id=str(uuid4()),
                agent="tagging_agent",
                provider=v.provider.value,
                severity=severity,
                title=f"Tag violation: {v.resource_id}",
                description=(
                    f"Resource {v.resource_id} ({v.resource_type}) in account "
                    f"{v.billing_account_id} is missing tags: {missing_str}. "
                    f"${savings_at_risk:.2f}/month cost at risk."
                    + (f" Inferred: {inferred}" if inferred else "")
                ),
                resource_ids=[v.resource_id],
                service_name=v.resource_type,
                current_monthly_cost=savings_at_risk,
                projected_monthly_savings=Decimal("0"),
                confidence_score=0.95,
                recommendation=(
                    f"Apply tags: {', '.join(v.missing_tags)}."
                    + (f" Suggested values: {inferred}" if inferred else "")
                ),
                action_type="tag",
                risk_level="low",
                tags={"resource_id": v.resource_id},
            ))

        logger.info("tagging_agent_complete", violations=len(violations), insights=len(insights))
        return insights

    async def scan_violations(self, time_range_days: int = 30) -> list[TagViolation]:
        """
        Query ClickHouse for resources with missing required tags.
        Returns TagViolation models.
        """
        import asyncio
        loop = asyncio.get_event_loop()

        # Resources with at least one empty required tag
        sql = """
        SELECT
            provider,
            billing_account_id,
            resource_id,
            resource_type,
            tags,
            sum(effective_cost) AS monthly_cost
        FROM focus_billing
        WHERE billing_period_start >= today() - INTERVAL %(days)s DAY
          AND resource_id != ''
        GROUP BY provider, billing_account_id, resource_id, resource_type, tags
        HAVING monthly_cost > 1.0
        ORDER BY monthly_cost DESC
        LIMIT 500
        """

        try:
            # Support both async (real client) and sync mock clients
            _exec = self._ch._client.execute
            import inspect
            if inspect.iscoroutinefunction(_exec):
                result = await _exec(sql, {"days": time_range_days}, with_column_types=True)
            else:
                result = await loop.run_in_executor(
                    None,
                    lambda: _exec(sql, {"days": time_range_days}, with_column_types=True),
                )
            columns = [c[0] for c in result[1]]
            rows = [dict(zip(columns, row)) for row in result[0]]
        except Exception as exc:
            logger.error("tagging_scan_query_failed", error=str(exc))
            return []

        violations: list[TagViolation] = []
        for row in rows:
            tags: dict[str, str] = {}
            raw_tags = row.get("tags", {})
            if isinstance(raw_tags, dict):
                tags = {k.lower(): str(v) for k, v in raw_tags.items()}
            elif isinstance(raw_tags, str):
                import json
                try:
                    tags = json.loads(raw_tags)
                except Exception:
                    tags = {}

            missing = [t for t in REQUIRED_TAGS if t not in tags or not tags[t]]
            non_compliant: dict[str, str] = {}

            # Check environment values
            env_val = tags.get("environment", "")
            if env_val and env_val not in ("development", "staging", "production", "dev", "prod"):
                non_compliant["environment"] = env_val

            if not missing and not non_compliant:
                continue  # resource is fully compliant

            try:
                provider = CloudProvider(row.get("provider", "aws").lower())
            except ValueError:
                provider = CloudProvider.AWS

            violations.append(TagViolation(
                provider=provider,
                resource_id=row.get("resource_id", ""),
                resource_type=row.get("resource_type") or None,
                billing_account_id=row.get("billing_account_id", ""),
                missing_tags=missing,
                non_compliant_tags=non_compliant,
                monthly_cost_at_risk=Decimal(str(row.get("monthly_cost", 0))),
            ))

        return violations

    async def infer_tags(
        self,
        resource_id: str,
        resource_type: str,
        billing_account_id: str,
    ) -> dict[str, str]:
        """
        Use Claude to infer missing tags from resource metadata.
        Returns dict of tag_key → inferred_value.
        """
        if not self._settings.anthropic_api_key:
            logger.warning("tagging_llm_skip", reason="No API key configured")
            return {t: "unknown" for t in REQUIRED_TAGS}

        prompt = (
            f"Resource ID: {resource_id}\n"
            f"Resource type: {resource_type}\n"
            f"Billing account: {billing_account_id}\n"
            f"Required tags: {', '.join(REQUIRED_TAGS)}"
        )

        try:
            import asyncio
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self._llm.invoke([
                    SystemMessage(content=TAG_INFERENCE_SYSTEM),
                    HumanMessage(content=prompt),
                ])
            )
            import json
            inferred = json.loads(response.content)
            logger.debug("tagging_inferred", resource_id=resource_id, inferred=inferred)
            return {k: str(v) for k, v in inferred.items() if k in REQUIRED_TAGS}
        except Exception as exc:
            logger.warning("tagging_inference_failed", resource_id=resource_id, error=str(exc))
            return {t: "unknown" for t in REQUIRED_TAGS}

    async def apply_tags(
        self,
        provider: CloudProvider,
        resource_id: str,
        tags: dict[str, str],
        region: str = "us-east-1",
    ) -> dict[str, Any]:
        """
        Apply tags to a cloud resource (OPA-gated write operation).
        Currently implemented for AWS; stubs for Azure/GCP.
        """
        # OPA gate — tagging is low-risk but still goes through policy
        from cloudsense.agents.shared_types import InsightSeverity, InsightStatus

        gate_insight = CostInsight(
            insight_id=str(uuid4()),
            agent="tagging_agent",
            provider=provider.value,
            severity=InsightSeverity.LOW,
            title=f"Apply tags to {resource_id}",
            description=f"Tagging: {tags}",
            resource_ids=[resource_id],
            action_type="tag",
            risk_level="low",
            confidence_score=1.0,
        )
        policy_result = await self._policy.evaluate(gate_insight)
        if not policy_result.get("allowed", False):
            return {"status": "denied", "reason": policy_result.get("reason")}

        if provider == CloudProvider.AWS:
            return await self._apply_aws_tags(resource_id=resource_id, tags=tags, region=region)
        if provider == CloudProvider.AZURE:
            return {"status": "simulated", "provider": "azure", "resource_id": resource_id, "tags": tags}
        if provider == CloudProvider.GCP:
            return {"status": "simulated", "provider": "gcp", "resource_id": resource_id, "tags": tags}

        return {"status": "error", "reason": f"Unknown provider: {provider}"}

    async def _apply_aws_tags(
        self, resource_id: str, tags: dict[str, str], region: str
    ) -> dict[str, Any]:
        import asyncio
        import boto3
        from cloudsense.services.api.config import get_settings as _gs
        s = _gs()
        session = boto3.Session(
            aws_access_key_id=(s.aws_access_key_id.get_secret_value() if s.aws_access_key_id else None),
            aws_secret_access_key=(s.aws_secret_access_key.get_secret_value() if s.aws_secret_access_key else None),
            region_name=region,
        )
        ec2 = session.client("ec2", region_name=region)
        tag_list = [{"Key": k, "Value": v} for k, v in tags.items()]

        def _tag() -> dict:
            ec2.create_tags(Resources=[resource_id], Tags=tag_list)
            return {"status": "applied", "provider": "aws",
                    "resource_id": resource_id, "tags": tags}

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(None, _tag)
            logger.info("aws_tags_applied", resource_id=resource_id, tags=tags)
            return result
        except Exception as exc:
            logger.error("aws_tags_failed", resource_id=resource_id, error=str(exc))
            return {"status": "error", "provider": "aws", "resource_id": resource_id, "error": str(exc)}

    async def compliance_report(self, time_range_days: int = 30) -> dict[str, Any]:
        """
        Generate a tag compliance summary for reporting / dashboards.
        """
        violations = await self.scan_violations(time_range_days)
        total_cost_at_risk = sum(v.monthly_cost_at_risk for v in violations)
        severity_counts = {"none": 0, "low": 0, "medium": 0, "high": 0}
        for v in violations:
            sev = v.severity
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

        return {
            "total_violations": len(violations),
            "total_cost_at_risk_monthly": float(total_cost_at_risk),
            "severity_breakdown": severity_counts,
            "required_tags": REQUIRED_TAGS,
            "top_violations": [
                {
                    "resource_id": v.resource_id,
                    "missing_tags": v.missing_tags,
                    "monthly_cost": float(v.monthly_cost_at_risk),
                    "severity": v.severity,
                }
                for v in sorted(violations, key=lambda x: x.monthly_cost_at_risk, reverse=True)[:20]
            ],
        }


# ── Helpers ─────────────────────────────────────────────────────

def _violation_severity(v: TagViolation) -> InsightSeverity:
    sev_str = v.severity
    return {
        "high": InsightSeverity.HIGH,
        "medium": InsightSeverity.MEDIUM,
        "low": InsightSeverity.LOW,
    }.get(sev_str, InsightSeverity.INFO)
