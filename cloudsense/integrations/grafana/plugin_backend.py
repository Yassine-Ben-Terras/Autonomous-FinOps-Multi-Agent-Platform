"""
Grafana Plugin Backend (Phase 5.2).

Implements the Grafana datasource plugin backend API so the CloudSense
Grafana plugin (published on grafana.com/plugins) can query billing data.

Grafana datasource protocol:
  POST /grafana/query          — time-series or table query
  POST /grafana/search         — metric name search (autocomplete)
  GET  /grafana/annotations    — anomaly annotations on time-series panels
  GET  /grafana/health         — datasource health check

Query types supported:
  timeseries  — cost over time per service/provider/region
  table       — cost breakdown table (drilldown)
  annotation  — anomaly markers on existing panels
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import structlog

from cloudsense.services.db.clickhouse import ClickHouseClient

logger = structlog.get_logger()


class GrafanaPluginBackend:
    """
    Handles Grafana datasource plugin protocol v2 requests.

    All responses follow Grafana's plugin data frame format so they
    render correctly in TimeSeries, Table, and Stat panels.
    """

    def __init__(self, ch: ClickHouseClient) -> None:
        self._ch = ch

    # ── Health ────────────────────────────────────────────────────

    async def health(self) -> dict[str, Any]:
        """Grafana health check — validates ClickHouse connectivity."""
        try:
            loop = asyncio.get_event_loop()
            _exec = self._ch._client.execute
            import inspect
            if inspect.iscoroutinefunction(_exec):
                await _exec("SELECT 1")
            else:
                await loop.run_in_executor(None, lambda: _exec("SELECT 1"))
            return {"status": "ok", "message": "CloudSense datasource is healthy"}
        except Exception as exc:
            logger.error("grafana_health_failed", error=str(exc))
            return {"status": "error", "message": str(exc)}

    # ── Metric Search (autocomplete) ──────────────────────────────

    async def search(self, query: str = "") -> list[str]:
        """
        Return list of metric names matching the search query.
        Used by Grafana panel editor for metric autocomplete.
        """
        base_metrics = [
            "cost.total",
            "cost.aws",
            "cost.azure",
            "cost.gcp",
            "cost.by_service",
            "cost.by_region",
            "cost.by_account",
            "savings.potential",
            "anomaly.count",
            "anomaly.cost_delta",
            "forecast.30d",
            "forecast.60d",
            "forecast.90d",
            "k8s.namespace.cost",
            "k8s.workload.cost",
            "tag.compliance.violations",
        ]
        q = query.lower()
        return [m for m in base_metrics if not q or q in m]

    # ── Query ─────────────────────────────────────────────────────

    async def query(self, request: dict[str, Any]) -> dict[str, Any]:
        """
        Handle Grafana query request.

        Request schema (Grafana datasource protocol v2):
        {
          "range": {"from": "...", "to": "..."},
          "targets": [
            {"target": "cost.total", "type": "timeseries", "refId": "A",
             "dimensions": {"provider": "aws", "service": "EC2"}}
          ],
          "maxDataPoints": 300,
          "intervalMs": 86400000
        }
        """
        results = []
        range_from = request.get("range", {}).get("from", "")
        range_to = request.get("range", {}).get("to", "")
        start_date = _parse_grafana_time(range_from)
        end_date = _parse_grafana_time(range_to)

        for target in request.get("targets", []):
            metric = target.get("target", "cost.total")
            query_type = target.get("type", "timeseries")
            dimensions = target.get("dimensions", {})
            ref_id = target.get("refId", "A")

            if query_type == "timeseries":
                frame = await self._timeseries_frame(
                    metric=metric,
                    start_date=start_date,
                    end_date=end_date,
                    dimensions=dimensions,
                    ref_id=ref_id,
                )
            elif query_type == "table":
                frame = await self._table_frame(
                    metric=metric,
                    start_date=start_date,
                    end_date=end_date,
                    dimensions=dimensions,
                    ref_id=ref_id,
                )
            else:
                frame = _empty_frame(ref_id)

            results.append(frame)

        return {"results": {r["refId"]: r for r in results}}

    # ── Annotations ───────────────────────────────────────────────

    async def annotations(
        self, start_date: str, end_date: str, query: str = "anomaly"
    ) -> list[dict[str, Any]]:
        """
        Return cost anomalies as Grafana annotation objects.
        These appear as vertical markers on TimeSeries panels.
        """
        sql = f"""
        SELECT
            billing_period_start AS ts,
            service_name,
            provider,
            effective_cost,
            list_cost
        FROM focus_billing
        WHERE billing_period_start >= '{start_date}'
          AND billing_period_start <= '{end_date}'
          AND (effective_cost / list_cost) > 1.3
          AND list_cost > 100
        ORDER BY effective_cost DESC
        LIMIT 100
        """
        rows = await self._run_query(sql, "grafana_annotations_failed")
        annotations = []
        for row in rows:
            delta = float(row.get("effective_cost", 0)) - float(row.get("list_cost", 0))
            annotations.append({
                "time": _to_unix_ms(row.get("ts")),
                "title": f"Cost spike: {row.get('service_name', 'Unknown')}",
                "text": (
                    f"Provider: {row.get('provider', '')}\n"
                    f"Effective: ${float(row.get('effective_cost', 0)):.2f}\n"
                    f"Delta vs list: +${delta:.2f}"
                ),
                "tags": ["anomaly", row.get("provider", ""), row.get("service_name", "")],
            })
        return annotations

    # ── Time-series frame builder ─────────────────────────────────

    async def _timeseries_frame(
        self,
        metric: str,
        start_date: str,
        end_date: str,
        dimensions: dict[str, Any],
        ref_id: str,
    ) -> dict[str, Any]:
        """Build a Grafana time-series data frame from ClickHouse."""
        provider_filter = f"AND provider = '{dimensions['provider']}'" if "provider" in dimensions else ""
        service_filter = f"AND service_name = '{dimensions['service']}'" if "service" in dimensions else ""
        region_filter = f"AND region_id = '{dimensions['region']}'" if "region" in dimensions else ""

        # Group by day
        sql = f"""
        SELECT
            toDate(billing_period_start) AS day,
            sum(effective_cost)          AS cost
        FROM focus_billing
        WHERE billing_period_start >= '{start_date}'
          AND billing_period_start <= '{end_date}'
          {provider_filter}
          {service_filter}
          {region_filter}
        GROUP BY day
        ORDER BY day ASC
        """
        rows = await self._run_query(sql, "grafana_timeseries_failed")

        times = [_to_unix_ms(r.get("day")) for r in rows]
        values = [float(r.get("cost", 0)) for r in rows]

        return {
            "refId": ref_id,
            "frames": [
                {
                    "schema": {
                        "name": metric,
                        "fields": [
                            {"name": "Time",  "type": "time",   "typeInfo": {"frame": "time.Time"}},
                            {"name": "Value", "type": "number", "typeInfo": {"frame": "float64"},
                             "labels": dimensions},
                        ],
                    },
                    "data": {"values": [times, values]},
                }
            ],
        }

    async def _table_frame(
        self,
        metric: str,
        start_date: str,
        end_date: str,
        dimensions: dict[str, Any],
        ref_id: str,
    ) -> dict[str, Any]:
        """Build a Grafana table data frame — cost breakdown."""
        group_by = dimensions.get("group_by", "service_name")
        valid_groups = {"service_name", "provider", "region_id", "billing_account_id", "resource_type"}
        if group_by not in valid_groups:
            group_by = "service_name"

        sql = f"""
        SELECT
            {group_by}          AS dimension,
            sum(effective_cost) AS total_cost,
            sum(list_cost)      AS list_cost,
            count()             AS row_count
        FROM focus_billing
        WHERE billing_period_start >= '{start_date}'
          AND billing_period_start <= '{end_date}'
        GROUP BY {group_by}
        ORDER BY total_cost DESC
        LIMIT 50
        """
        rows = await self._run_query(sql, "grafana_table_failed")

        return {
            "refId": ref_id,
            "frames": [
                {
                    "schema": {
                        "name": metric,
                        "fields": [
                            {"name": group_by,      "type": "string"},
                            {"name": "total_cost",  "type": "number"},
                            {"name": "list_cost",   "type": "number"},
                            {"name": "row_count",   "type": "number"},
                        ],
                    },
                    "data": {
                        "values": [
                            [r.get("dimension", "") for r in rows],
                            [float(r.get("total_cost", 0)) for r in rows],
                            [float(r.get("list_cost", 0)) for r in rows],
                            [r.get("row_count", 0) for r in rows],
                        ]
                    },
                }
            ],
        }

    # ── Shared ────────────────────────────────────────────────────

    async def _run_query(self, sql: str, err_label: str) -> list[dict[str, Any]]:
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
            logger.error(err_label, error=str(exc))
            return []


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_grafana_time(ts: str) -> str:
    """Convert Grafana ISO timestamp to YYYY-MM-DD."""
    if not ts:
        return "2024-01-01"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return ts[:10]


def _to_unix_ms(val: Any) -> int:
    """Convert date/datetime to Unix milliseconds for Grafana."""
    if val is None:
        return 0
    if isinstance(val, datetime):
        return int(val.timestamp() * 1000)
    if hasattr(val, "timetuple"):
        import calendar
        return calendar.timegm(val.timetuple()) * 1000
    try:
        dt = datetime.fromisoformat(str(val))
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0


def _empty_frame(ref_id: str) -> dict[str, Any]:
    return {
        "refId": ref_id,
        "frames": [{"schema": {"fields": []}, "data": {"values": []}}],
    }
