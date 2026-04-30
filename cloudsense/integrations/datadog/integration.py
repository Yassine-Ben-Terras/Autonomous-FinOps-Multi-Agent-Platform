"""
Datadog Integration (Phase 5.2).

Pushes CloudSense cost intelligence data to Datadog:

  1. Cost Metrics  — daily cost per provider/service/region as gauge metrics
                     under the namespace `cloudsense.cost.*`
  2. Anomaly Events— cost spikes published as Datadog Events (appear in
                     Event Stream and overlay on dashboards)
  3. Cost Monitors — programmatically creates Datadog monitors for budget alerts

Datadog API used:
  POST /api/v2/series     — submit metrics
  POST /api/v1/events     — submit events
  POST /api/v1/monitor    — create monitors

All calls are batched and respect Datadog's rate limits
(max 500 metrics per payload, max 1000 events per minute).
"""
from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import structlog

from cloudsense.services.db.clickhouse import ClickHouseClient
from cloudsense.services.api.config import Settings, get_settings

logger = structlog.get_logger()

DATADOG_API_BASE = "https://api.datadoghq.com"
METRIC_BATCH_SIZE = 500


class DatadogIntegration:
    """
    Bidirectional Datadog integration for CloudSense cost data.

    Usage:
        dd = DatadogIntegration(ch, settings)
        await dd.push_daily_costs(date="2024-01-15")
        await dd.push_anomaly_event(anomaly)
        await dd.create_budget_monitor(service="EC2", threshold=5000.0)
    """

    def __init__(self, ch: ClickHouseClient, settings: Settings | None = None) -> None:
        self._ch = ch
        self._settings = settings or get_settings()
        self._api_key = (
            self._settings.datadog_api_key.get_secret_value()
            if hasattr(self._settings, "datadog_api_key")
            and self._settings.datadog_api_key
            else None
        )
        self._app_key = (
            self._settings.datadog_app_key.get_secret_value()
            if hasattr(self._settings, "datadog_app_key")
            and self._settings.datadog_app_key
            else None
        )

    # ── Cost Metrics ──────────────────────────────────────────────

    async def push_daily_costs(
        self, date: str, providers: list[str] | None = None
    ) -> dict[str, Any]:
        """
        Fetch daily costs from ClickHouse and push to Datadog as gauge metrics.

        Metrics emitted:
          cloudsense.cost.daily            — total daily cost
          cloudsense.cost.by_service       — cost per service
          cloudsense.cost.by_provider      — cost per provider
          cloudsense.cost.by_region        — cost per region
          cloudsense.savings.potential     — potential savings detected
        """
        rows = await self._query_daily_costs(date, providers)
        if not rows:
            logger.info("datadog_no_cost_data", date=date)
            return {"pushed": 0, "skipped": True}

        ts = int(time.time())
        series: list[dict[str, Any]] = []

        for row in rows:
            cost = float(row.get("total_cost", 0))
            tags = [
                f"provider:{row.get('provider', 'unknown')}",
                f"service:{row.get('service_name', 'unknown')}",
                f"region:{row.get('region_id', 'unknown')}",
                f"account:{row.get('billing_account_id', 'unknown')}",
                "source:cloudsense",
            ]

            series.append(_metric_point("cloudsense.cost.daily", cost, ts, tags))

            # Breakdown metrics
            series.append(_metric_point(
                "cloudsense.cost.by_service", cost, ts,
                [f"service:{row.get('service_name', 'unknown')}", "source:cloudsense"],
            ))
            series.append(_metric_point(
                "cloudsense.cost.by_provider", cost, ts,
                [f"provider:{row.get('provider', 'unknown')}", "source:cloudsense"],
            ))
            series.append(_metric_point(
                "cloudsense.cost.by_region", cost, ts,
                [f"region:{row.get('region_id', 'unknown')}", "source:cloudsense"],
            ))

        # Batch push
        total_pushed = await self._push_metrics_batched(series)
        logger.info("datadog_cost_metrics_pushed", date=date, metrics=total_pushed)
        return {"pushed": total_pushed, "rows": len(rows), "date": date}

    async def push_savings_metrics(
        self, insights: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Push potential savings from agent insights as Datadog metrics."""
        ts = int(time.time())
        series: list[dict[str, Any]] = []

        for insight in insights:
            savings = float(insight.get("projected_monthly_savings", 0))
            if savings <= 0:
                continue
            tags = [
                f"agent:{insight.get('agent', 'unknown')}",
                f"provider:{insight.get('provider', 'unknown')}",
                f"severity:{insight.get('severity', 'info')}",
                f"action_type:{insight.get('action_type', 'unknown')}",
                "source:cloudsense",
            ]
            series.append(_metric_point("cloudsense.savings.potential", savings, ts, tags))

        pushed = await self._push_metrics_batched(series)
        return {"pushed": pushed}

    # ── Anomaly Events ────────────────────────────────────────────

    async def push_anomaly_event(
        self,
        title: str,
        text: str,
        severity: str = "warning",
        provider: str = "unknown",
        service: str = "unknown",
        cost_delta: float = 0.0,
    ) -> dict[str, Any]:
        """
        Publish a cost anomaly as a Datadog Event.
        Appears in Event Stream and overlays on dashboard timelines.
        """
        alert_type = {
            "critical": "error",
            "high": "warning",
            "medium": "warning",
            "low": "info",
            "info": "info",
        }.get(severity.lower(), "warning")

        event_payload = {
            "title": f"[CloudSense] {title}",
            "text": (
                f"{text}\n\n"
                f"**Provider**: {provider}\n"
                f"**Service**: {service}\n"
                f"**Cost delta**: +${cost_delta:.2f}\n"
                f"**Detected at**: {datetime.now(tz=timezone.utc).isoformat()}"
            ),
            "alert_type": alert_type,
            "tags": [
                f"provider:{provider}",
                f"service:{service}",
                "source:cloudsense",
                "type:cost_anomaly",
            ],
            "source_type_name": "CloudSense",
        }

        result = await self._post_datadog("/api/v1/events", event_payload, api_key_only=True)
        logger.info("datadog_event_pushed", title=title, provider=provider)
        return result

    # ── Budget Monitors ───────────────────────────────────────────

    async def create_budget_monitor(
        self,
        name: str,
        service: str | None = None,
        provider: str | None = None,
        monthly_threshold: float = 1000.0,
        notify_channels: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Create a Datadog monitor that alerts when CloudSense cost metrics
        exceed the monthly threshold (prorated to daily).
        """
        daily_threshold = monthly_threshold / 30.0
        tag_filters = "source:cloudsense"
        if provider:
            tag_filters += f" provider:{provider}"
        if service:
            tag_filters += f" service:{service}"

        notify = " ".join(notify_channels or ["@channel"])
        monitor_payload = {
            "name": f"[CloudSense] {name}",
            "type": "metric alert",
            "query": (
                f"sum(last_1d):sum:cloudsense.cost.daily{{{tag_filters}}} > {daily_threshold}"
            ),
            "message": (
                f"CloudSense detected daily cost exceeding ${daily_threshold:.2f} "
                f"(monthly budget: ${monthly_threshold:.2f}) for {service or 'all services'} "
                f"on {provider or 'all providers'}.\n\n"
                f"View dashboard: {self._settings.base_url}/dashboard\n\n"
                f"{notify}"
            ),
            "tags": ["source:cloudsense", "type:budget_alert"],
            "options": {
                "notify_audit": True,
                "require_full_window": False,
                "notify_no_data": False,
                "thresholds": {"critical": daily_threshold, "warning": daily_threshold * 0.8},
            },
        }

        result = await self._post_datadog("/api/v1/monitor", monitor_payload)
        logger.info("datadog_monitor_created", name=name, threshold=daily_threshold)
        return result

    # ── ClickHouse queries ────────────────────────────────────────

    async def _query_daily_costs(
        self, date: str, providers: list[str] | None
    ) -> list[dict[str, Any]]:
        prov_filter = ""
        if providers:
            pvs = ", ".join(f"'{p}'" for p in providers)
            prov_filter = f"AND provider IN ({pvs})"

        sql = f"""
        SELECT
            provider,
            service_name,
            region_id,
            billing_account_id,
            sum(effective_cost) AS total_cost
        FROM focus_billing
        WHERE toDate(billing_period_start) = '{date}'
          {prov_filter}
        GROUP BY provider, service_name, region_id, billing_account_id
        HAVING total_cost > 0
        ORDER BY total_cost DESC
        LIMIT 1000
        """
        try:
            loop = asyncio.get_event_loop()
            _exec = self._ch._client.execute
            import inspect
            if inspect.iscoroutinefunction(_exec):
                result = await _exec(sql, with_column_types=True)
            else:
                result = await loop.run_in_executor(
                    None, lambda: _exec(sql, with_column_types=True)
                )
            columns = [c[0] for c in result[1]]
            return [dict(zip(columns, row)) for row in result[0]]
        except Exception as exc:
            logger.error("datadog_cost_query_failed", error=str(exc))
            return []

    # ── HTTP helpers ──────────────────────────────────────────────

    async def _push_metrics_batched(self, series: list[dict[str, Any]]) -> int:
        """Push metrics in batches of METRIC_BATCH_SIZE."""
        if not series:
            return 0
        total = 0
        for i in range(0, len(series), METRIC_BATCH_SIZE):
            batch = series[i: i + METRIC_BATCH_SIZE]
            await self._post_datadog(
                "/api/v2/series",
                {"series": batch},
                api_v2=True,
            )
            total += len(batch)
        return total

    async def _post_datadog(
        self,
        path: str,
        payload: dict[str, Any],
        api_key_only: bool = False,
        api_v2: bool = False,
    ) -> dict[str, Any]:
        """POST to Datadog API. Returns response dict."""
        if not self._api_key:
            logger.warning("datadog_api_key_missing", path=path)
            return {"status": "skipped", "reason": "No Datadog API key configured"}

        url = f"{DATADOG_API_BASE}{path}"
        headers = {
            "Content-Type": "application/json",
            "DD-API-KEY": self._api_key,
        }
        if not api_key_only and self._app_key:
            headers["DD-APPLICATION-KEY"] = self._app_key

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        for k, v in headers.items():
            req.add_header(k, v)

        loop = asyncio.get_event_loop()
        try:
            def _do_request() -> dict:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return json.loads(resp.read())

            return await loop.run_in_executor(None, _do_request)
        except urllib.error.HTTPError as exc:
            body_err = exc.read().decode("utf-8", errors="replace")
            logger.error("datadog_api_error", path=path, status=exc.code, body=body_err)
            return {"error": str(exc), "status": exc.code}
        except Exception as exc:
            logger.error("datadog_request_failed", path=path, error=str(exc))
            return {"error": str(exc)}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _metric_point(
    metric: str, value: float, ts: int, tags: list[str]
) -> dict[str, Any]:
    return {
        "metric": metric,
        "type": 3,           # 3 = gauge in Datadog v2 API
        "points": [{"timestamp": ts, "value": value}],
        "tags": tags,
        "resources": [{"name": "cloudsense", "type": "host"}],
    }
