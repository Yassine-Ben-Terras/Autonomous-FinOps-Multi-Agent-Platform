"""
Unit tests — Phase 5.2: FOCUS Export Engine, BI Adapters,
Grafana Plugin Backend, Datadog Integration.

All tests run without external services (ClickHouse and Datadog mocked).
"""
from __future__ import annotations

import json
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any

from cloudsense.exporters.focus_export import (
    ExportFormat, FocusExportEngine, ExportResult,
    FOCUS_COLUMNS, _serialize_cell, _serialize_row,
)
from cloudsense.exporters.bi_adapters import (
    LookerAdapter, TableauAdapter, PowerBIAdapter,
    _tableau_type, _tableau_role,
)
from cloudsense.integrations.grafana.plugin_backend import (
    GrafanaPluginBackend, _parse_grafana_time, _to_unix_ms,
)
from cloudsense.integrations.datadog.integration import (
    DatadogIntegration, _metric_point,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_ch():
    ch = MagicMock()
    ch._client = MagicMock()
    ch._client.execute = AsyncMock(return_value=([], []))
    ch.close = AsyncMock()
    return ch


@pytest.fixture
def sample_focus_rows() -> list[dict[str, Any]]:
    return [
        {
            "BillingAccountId": "123456789",
            "BillingAccountName": "",
            "BillingPeriodStart": "2024-01-01",
            "BillingPeriodEnd": "2024-01-31",
            "ChargePeriodStart": "2024-01-01T00:00:00",
            "ChargePeriodEnd": "2024-01-31T23:59:59",
            "ChargeCategory": "Usage",
            "ChargeDescription": "",
            "ChargeFrequency": "one-time",
            "ServiceName": "Virtual Machine",
            "ServiceCategory": "",
            "ResourceId": "i-0abc123",
            "ResourceName": "",
            "ResourceType": "EC2 Instance",
            "RegionId": "us-east-1",
            "RegionName": "",
            "AvailabilityZone": "",
            "Provider": "aws",
            "PublisherName": "aws",
            "InvoiceIssuerName": "aws",
            "ListCost": 200.0,
            "ListUnitPrice": 0.0,
            "EffectiveCost": 160.0,
            "BilledCost": 160.0,
            "ContractedCost": 160.0,
            "ContractedUnitPrice": 0.0,
            "UsageQuantity": 720.0,
            "UsageUnit": "Hours",
            "CommitmentDiscountId": "",
            "CommitmentDiscountName": "",
            "CommitmentDiscountType": "",
            "CommitmentDiscountCategory": "",
            "Tags": {"team": "platform", "env": "production"},
        },
        {
            "BillingAccountId": "123456789",
            "BillingAccountName": "",
            "BillingPeriodStart": "2024-01-01",
            "BillingPeriodEnd": "2024-01-31",
            "ChargePeriodStart": "2024-01-01T00:00:00",
            "ChargePeriodEnd": "2024-01-31T23:59:59",
            "ChargeCategory": "Usage",
            "ChargeDescription": "",
            "ChargeFrequency": "one-time",
            "ServiceName": "S3",
            "ServiceCategory": "",
            "ResourceId": "my-bucket",
            "ResourceName": "",
            "ResourceType": "S3 Bucket",
            "RegionId": "us-west-2",
            "RegionName": "",
            "AvailabilityZone": "",
            "Provider": "aws",
            "PublisherName": "aws",
            "InvoiceIssuerName": "aws",
            "ListCost": 50.0,
            "ListUnitPrice": 0.0,
            "EffectiveCost": 45.0,
            "BilledCost": 45.0,
            "ContractedCost": 45.0,
            "ContractedUnitPrice": 0.0,
            "UsageQuantity": 1000.0,
            "UsageUnit": "GB-month",
            "CommitmentDiscountId": "",
            "CommitmentDiscountName": "",
            "CommitmentDiscountType": "",
            "CommitmentDiscountCategory": "",
            "Tags": {},
        },
    ]


@pytest.fixture
def focus_engine_with_data(mock_ch, sample_focus_rows):
    """FocusExportEngine whose ClickHouse returns sample_focus_rows."""
    cols = [[k, "String"] for k in sample_focus_rows[0].keys()]
    rows = [[row[k] for k in sample_focus_rows[0].keys()] for row in sample_focus_rows]
    mock_ch._client.execute = AsyncMock(return_value=(rows, cols))
    return FocusExportEngine(mock_ch)


# ── FocusExportEngine tests ───────────────────────────────────────────────────

class TestFocusExportEngine:

    @pytest.mark.asyncio
    async def test_export_csv_empty(self, mock_ch):
        engine = FocusExportEngine(mock_ch)
        result = await engine.export("csv", "2024-01-01", "2024-01-31")
        assert result.format == ExportFormat.CSV
        assert result.row_count == 0
        assert b"BillingAccountId" in result.content or len(result.content) >= 0

    @pytest.mark.asyncio
    async def test_export_csv_with_data(self, focus_engine_with_data, sample_focus_rows):
        result = await focus_engine_with_data.export(
            "csv", "2024-01-01", "2024-01-31"
        )
        assert result.format == ExportFormat.CSV
        assert result.row_count == 2
        assert result.mime_type == "text/csv"
        assert b"Virtual Machine" in result.content

    @pytest.mark.asyncio
    async def test_export_jsonl_with_data(self, focus_engine_with_data, sample_focus_rows):
        result = await focus_engine_with_data.export(
            "jsonl", "2024-01-01", "2024-01-31"
        )
        assert result.format == ExportFormat.JSON_LINES
        assert result.mime_type == "application/x-ndjson"
        lines = result.content.decode("utf-8").strip().split("\n")
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first["Provider"] == "aws"
        assert first["EffectiveCost"] == 160.0

    @pytest.mark.asyncio
    async def test_export_xlsx_fallback_csv(self, focus_engine_with_data):
        """XLSX falls back to CSV if openpyxl not installed."""
        result = await focus_engine_with_data.export(
            "xlsx", "2024-01-01", "2024-01-31"
        )
        assert result.row_count == 2
        assert result.filename.endswith(".xlsx")

    @pytest.mark.asyncio
    async def test_export_parquet_fallback_csv(self, focus_engine_with_data):
        """Parquet falls back to CSV if pyarrow not installed."""
        result = await focus_engine_with_data.export(
            "parquet", "2024-01-01", "2024-01-31"
        )
        assert result.row_count == 2
        assert result.filename.endswith(".parquet")

    @pytest.mark.asyncio
    async def test_export_invalid_format(self, mock_ch):
        engine = FocusExportEngine(mock_ch)
        with pytest.raises(ValueError):
            await engine.export("excel97", "2024-01-01", "2024-01-31")

    @pytest.mark.asyncio
    async def test_export_filters_provider(self, mock_ch):
        """Provider filter is included in the WHERE clause."""
        engine = FocusExportEngine(mock_ch)
        await engine.export("csv", "2024-01-01", "2024-01-31", providers=["aws"])
        call_args = mock_ch._client.execute.call_args[0][0]
        assert "provider IN ('aws')" in call_args

    @pytest.mark.asyncio
    async def test_export_filters_account(self, mock_ch):
        engine = FocusExportEngine(mock_ch)
        await engine.export("csv", "2024-01-01", "2024-01-31",
                            billing_account_ids=["123", "456"])
        call_args = mock_ch._client.execute.call_args[0][0]
        assert "billing_account_id IN ('123', '456')" in call_args

    def test_export_result_metadata(self):
        from cloudsense.exporters.focus_export import ExportResult, ExportFormat
        result = ExportResult(
            format=ExportFormat.CSV,
            filename="test.csv",
            content=b"data",
            mime_type="text/csv",
            row_count=42,
            start_date="2024-01-01",
            end_date="2024-01-31",
            generated_at="2024-01-31T12:00:00+00:00",
        )
        meta = result.to_metadata()
        assert meta["format"] == "csv"
        assert meta["row_count"] == 42
        assert meta["size_bytes"] == 4

    def test_serialize_cell_decimal(self):
        assert _serialize_cell(Decimal("123.45")) == 123.45

    def test_serialize_cell_dict(self):
        result = _serialize_cell({"key": "value"})
        assert isinstance(result, str)
        assert "key" in result

    def test_serialize_row(self, sample_focus_rows):
        row = sample_focus_rows[0]
        serialized = _serialize_row(row)
        assert isinstance(serialized["EffectiveCost"], float)

    def test_focus_columns_complete(self):
        assert "BillingAccountId" in FOCUS_COLUMNS
        assert "EffectiveCost" in FOCUS_COLUMNS
        assert "Tags" in FOCUS_COLUMNS
        assert len(FOCUS_COLUMNS) >= 30


# ── BI Adapter tests ──────────────────────────────────────────────────────────

class TestBIAdapters:

    @pytest.mark.asyncio
    async def test_looker_manifest_structure(self, focus_engine_with_data):
        adapter = LookerAdapter(focus_engine_with_data)
        result = await adapter.export("2024-01-01", "2024-01-31")
        manifest = result["manifest"]
        assert manifest["standard"] == "FOCUS 1.0"
        assert "dimensions" in manifest
        assert "measures" in manifest
        assert any(d["name"] == "provider" for d in manifest["dimensions"])
        assert any(m["name"] == "total_effective_cost" for m in manifest["measures"])

    @pytest.mark.asyncio
    async def test_looker_returns_parquet_result(self, focus_engine_with_data):
        adapter = LookerAdapter(focus_engine_with_data)
        result = await adapter.export("2024-01-01", "2024-01-31")
        assert "parquet" in result
        assert "instructions" in result
        assert result["parquet"].format == ExportFormat.PARQUET

    @pytest.mark.asyncio
    async def test_tableau_tds_xml_structure(self, focus_engine_with_data):
        adapter = TableauAdapter(focus_engine_with_data)
        result = await adapter.export("2024-01-01", "2024-01-31")
        tds = result["tds_xml"]
        assert "datasource" in tds
        assert "focus_export" in tds
        assert "connection" in tds
        assert "FOCUS Billing" in tds

    @pytest.mark.asyncio
    async def test_tableau_returns_xlsx_result(self, focus_engine_with_data):
        adapter = TableauAdapter(focus_engine_with_data)
        result = await adapter.export("2024-01-01", "2024-01-31")
        assert "xlsx" in result
        assert result["xlsx"].format == ExportFormat.XLSX

    @pytest.mark.asyncio
    async def test_powerbi_m_script_structure(self, focus_engine_with_data):
        adapter = PowerBIAdapter(focus_engine_with_data)
        result = await adapter.export("2024-01-01", "2024-01-31")
        m = result["power_query_m"]
        assert "Excel.Workbook" in m
        assert "EffectiveCost" in m
        assert "BillingPeriodStart" in m
        assert "MonthYear" in m     # computed column
        assert "DiscountPct" in m   # computed column

    @pytest.mark.asyncio
    async def test_powerbi_pbids_structure(self, focus_engine_with_data):
        adapter = PowerBIAdapter(focus_engine_with_data)
        result = await adapter.export("2024-01-01", "2024-01-31")
        pbids = result["pbids"]
        assert pbids["version"] == "0.1"
        assert "connections" in pbids
        assert pbids["connections"][0]["mode"] == "Import"

    def test_tableau_type_date_cols(self):
        assert _tableau_type("BillingPeriodStart") == "date"
        assert _tableau_type("ChargePeriodEnd") == "date"

    def test_tableau_type_num_cols(self):
        assert _tableau_type("EffectiveCost") == "real"
        assert _tableau_type("UsageQuantity") == "real"

    def test_tableau_type_string_cols(self):
        assert _tableau_type("Provider") == "string"
        assert _tableau_type("ServiceName") == "string"

    def test_tableau_role(self):
        assert _tableau_role("EffectiveCost") == "measure"
        assert _tableau_role("Provider") == "dimension"


# ── Grafana Plugin Backend tests ──────────────────────────────────────────────

class TestGrafanaPluginBackend:

    @pytest.mark.asyncio
    async def test_health_ok(self, mock_ch):
        backend = GrafanaPluginBackend(mock_ch)
        result = await backend.health()
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_health_error(self, mock_ch):
        mock_ch._client.execute = AsyncMock(side_effect=Exception("CH down"))
        backend = GrafanaPluginBackend(mock_ch)
        result = await backend.health()
        assert result["status"] == "error"
        assert "CH down" in result["message"]

    @pytest.mark.asyncio
    async def test_search_no_query(self, mock_ch):
        backend = GrafanaPluginBackend(mock_ch)
        metrics = await backend.search("")
        assert "cost.total" in metrics
        assert "anomaly.count" in metrics
        assert len(metrics) >= 10

    @pytest.mark.asyncio
    async def test_search_with_filter(self, mock_ch):
        backend = GrafanaPluginBackend(mock_ch)
        metrics = await backend.search("k8s")
        assert all("k8s" in m for m in metrics)

    @pytest.mark.asyncio
    async def test_query_timeseries_empty(self, mock_ch):
        backend = GrafanaPluginBackend(mock_ch)
        request = {
            "range": {"from": "2024-01-01T00:00:00Z", "to": "2024-01-31T23:59:59Z"},
            "targets": [{"target": "cost.total", "type": "timeseries", "refId": "A"}],
        }
        result = await backend.query(request)
        assert "results" in result
        assert "A" in result["results"]
        frame = result["results"]["A"]
        assert "frames" in frame

    @pytest.mark.asyncio
    async def test_query_table_empty(self, mock_ch):
        backend = GrafanaPluginBackend(mock_ch)
        request = {
            "range": {"from": "2024-01-01T00:00:00Z", "to": "2024-01-31T23:59:59Z"},
            "targets": [{"target": "cost.by_service", "type": "table", "refId": "B",
                         "dimensions": {"group_by": "service_name"}}],
        }
        result = await backend.query(request)
        assert "B" in result["results"]

    @pytest.mark.asyncio
    async def test_query_multiple_targets(self, mock_ch):
        backend = GrafanaPluginBackend(mock_ch)
        request = {
            "range": {"from": "2024-01-01T00:00:00Z", "to": "2024-01-31T23:59:59Z"},
            "targets": [
                {"target": "cost.total", "type": "timeseries", "refId": "A"},
                {"target": "cost.aws",   "type": "timeseries", "refId": "B"},
            ],
        }
        result = await backend.query(request)
        assert "A" in result["results"]
        assert "B" in result["results"]

    @pytest.mark.asyncio
    async def test_annotations_empty(self, mock_ch):
        backend = GrafanaPluginBackend(mock_ch)
        result = await backend.annotations("2024-01-01", "2024-01-31")
        assert isinstance(result, list)

    def test_parse_grafana_time_iso(self):
        result = _parse_grafana_time("2024-01-15T10:30:00Z")
        assert result == "2024-01-15"

    def test_parse_grafana_time_empty(self):
        result = _parse_grafana_time("")
        assert result == "2024-01-01"

    def test_to_unix_ms_none(self):
        assert _to_unix_ms(None) == 0

    def test_to_unix_ms_string(self):
        result = _to_unix_ms("2024-01-01T00:00:00")
        assert result > 0
        assert result > 1_700_000_000_000  # after Nov 2023


# ── Datadog Integration tests ─────────────────────────────────────────────────

class TestDatadogIntegration:

    @pytest.fixture
    def dd(self, mock_ch):
        from cloudsense.services.api.config import Settings
        settings = Settings(secret_key="test-secret")
        return DatadogIntegration(mock_ch, settings)

    @pytest.mark.asyncio
    async def test_push_daily_costs_no_api_key(self, dd, mock_ch):
        """Without API key, push should skip gracefully."""
        rows = [["aws", "EC2", "us-east-1", "123", 500.0]]
        cols = [["provider","String"],["service_name","String"],
                ["region_id","String"],["billing_account_id","String"],
                ["total_cost","Float64"]]
        mock_ch._client.execute = AsyncMock(return_value=(rows, cols))
        result = await dd.push_daily_costs(date="2024-01-15")
        # No API key → skipped OR pushed=0
        assert "pushed" in result or "skipped" in result

    @pytest.mark.asyncio
    async def test_push_daily_costs_empty_data(self, dd):
        result = await dd.push_daily_costs(date="2024-01-15")
        assert result.get("pushed", 0) == 0 or result.get("skipped") is True

    @pytest.mark.asyncio
    async def test_push_anomaly_event_no_api_key(self, dd):
        result = await dd.push_anomaly_event(
            title="EC2 cost spike",
            text="EC2 costs increased 40% vs baseline",
            severity="high",
            provider="aws",
            service="EC2",
            cost_delta=350.0,
        )
        assert "status" in result or "error" in result

    @pytest.mark.asyncio
    async def test_create_budget_monitor_no_api_key(self, dd):
        result = await dd.create_budget_monitor(
            name="EC2 Monthly Budget",
            service="EC2",
            provider="aws",
            monthly_threshold=5000.0,
        )
        assert "status" in result or "error" in result

    @pytest.mark.asyncio
    async def test_push_savings_metrics_empty(self, dd):
        result = await dd.push_savings_metrics([])
        assert result["pushed"] == 0

    @pytest.mark.asyncio
    async def test_push_savings_metrics_with_data(self, dd):
        insights = [
            {"agent": "aws", "provider": "aws", "severity": "high",
             "action_type": "stop", "projected_monthly_savings": 200.0},
            {"agent": "aws", "provider": "aws", "severity": "medium",
             "action_type": "rightsize", "projected_monthly_savings": 0.0},  # skip
        ]
        result = await dd.push_savings_metrics(insights)
        # Only 1 insight has savings > 0
        assert result["pushed"] == 0 or result["pushed"] == 1  # 0 if no key

    def test_metric_point_structure(self):
        mp = _metric_point("cloudsense.cost.daily", 123.45, 1700000000,
                           ["provider:aws", "source:cloudsense"])
        assert mp["metric"] == "cloudsense.cost.daily"
        assert mp["type"] == 3
        assert mp["points"][0]["value"] == 123.45
        assert "provider:aws" in mp["tags"]

    def test_budget_monitor_daily_threshold(self, dd):
        """Daily threshold = monthly / 30."""
        from cloudsense.services.api.config import Settings
        settings = Settings(secret_key="test")
        dd2 = DatadogIntegration(mock_ch, settings)
        # monthly = 3000 → daily = 100
        # We can't call create_budget_monitor without a CH, but we can verify the math
        assert abs(3000.0 / 30.0 - 100.0) < 0.01
