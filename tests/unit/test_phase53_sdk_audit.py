"""
CloudSense Phase 5.3 — Unit Tests
Plugin SDK, Marketplace Registry & Installer, Audit Log Exporter.

All external services mocked — no credentials required.
Run: pytest tests/unit/test_phase53_sdk_audit.py -v
"""
from __future__ import annotations

import asyncio
import gzip
import json
import os
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────
# Plugin SDK tests
# ─────────────────────────────────────────────────────────────

class TestFocusRecord:
    def _make(self, **kw: Any):
        from cloudsense.sdk.plugin_sdk import FocusRecord
        defaults = dict(
            provider_name="aws",
            billing_account_id="123456789012",
            billing_period_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            billing_period_end=datetime(2024, 2, 1, tzinfo=timezone.utc),
            charge_period_start=datetime(2024, 1, 15, tzinfo=timezone.utc),
            charge_period_end=datetime(2024, 1, 16, tzinfo=timezone.utc),
            service_name="Amazon EC2",
            billed_cost=Decimal("100.00"),
            effective_cost=Decimal("80.00"),
            list_cost=Decimal("120.00"),
        )
        defaults.update(kw)
        return FocusRecord(**defaults)

    def test_basic_construction(self):
        record = self._make()
        assert record.provider_name == "aws"
        assert record.effective_cost == Decimal("80.00")
        assert record.tags == {}

    def test_tags_populated(self):
        record = self._make(tags={"team": "platform", "env": "production"})
        assert record.tags["team"] == "platform"

    def test_optional_fields_default_none(self):
        record = self._make()
        assert record.resource_id is None
        assert record.usage_quantity is None

    def test_commitment_discount_defaults(self):
        record = self._make()
        assert record.commitment_discount_type == "None"
        assert record.pricing_category == "On-Demand"


class TestPluginSDKRegistration:

    def test_connector_plugin_auto_registers(self):
        from cloudsense.sdk.plugin_sdk import ConnectorPlugin, FocusRecord, get_registry, PluginType
        registry = get_registry()

        class TestConnector(ConnectorPlugin):
            name    = "test-auto-register-connector"
            version = "1.0.0"
            description = "Test connector for SDK unit tests"

            async def fetch(self, start_date, end_date, account_id=None):
                return []

        # Auto-registration happens in __init_subclass__
        found = registry.get(PluginType.CONNECTOR, "test-auto-register-connector")
        assert found is TestConnector

    def test_plugin_manifest_populated(self):
        from cloudsense.sdk.plugin_sdk import ConnectorPlugin, PluginType

        class ManifestConnector(ConnectorPlugin):
            name        = "manifest-test-connector"
            version     = "2.3.1"
            description = "Manifest test"
            author      = "Test Author"
            async def fetch(self, *a, **kw): return []

        m = ManifestConnector.manifest()
        assert m.name        == "manifest-test-connector"
        assert m.version     == "2.3.1"
        assert m.plugin_type == PluginType.CONNECTOR

    def test_abstract_connector_cannot_instantiate(self):
        from cloudsense.sdk.plugin_sdk import ConnectorPlugin
        with pytest.raises(TypeError):
            ConnectorPlugin()

    def test_agent_plugin_registers_as_agent_type(self):
        from cloudsense.sdk.plugin_sdk import AgentPlugin, PluginType, get_registry

        class TestAgentPlugin(AgentPlugin):
            name = "test-agent-plugin"
            description = "Agent plugin test"
            async def analyze(self, billing_data, time_range_days=30, **ctx): return []

        registry = get_registry()
        found = registry.get(PluginType.AGENT, "test-agent-plugin")
        assert found is TestAgentPlugin

    def test_exporter_plugin_registers_correctly(self):
        from cloudsense.sdk.plugin_sdk import ExporterPlugin, PluginType, get_registry

        class TestExporter(ExporterPlugin):
            name = "test-exporter-plugin"
            description = "Exporter test"
            async def export(self, records, start_date, end_date, destination, **opts):
                return {"status": "ok", "records_exported": len(records)}

        registry = get_registry()
        found = registry.get(PluginType.EXPORTER, "test-exporter-plugin")
        assert found is TestExporter

    def test_alerter_plugin_registers_correctly(self):
        from cloudsense.sdk.plugin_sdk import AlerterPlugin, PluginType, get_registry

        class TestAlerter(AlerterPlugin):
            name = "test-alerter-plugin"
            description = "Alerter test"
            async def send(self, title, message, severity="warning", metadata=None):
                return {"status": "sent", "channel": "test"}

        registry = get_registry()
        found = registry.get(PluginType.ALERTER, "test-alerter-plugin")
        assert found is TestAlerter

    def test_list_all_returns_manifests(self):
        from cloudsense.sdk.plugin_sdk import get_registry
        manifests = get_registry().list_all()
        assert isinstance(manifests, list)
        assert len(manifests) >= 3   # at least the ones registered above

    def test_list_by_type_filters_correctly(self):
        from cloudsense.sdk.plugin_sdk import get_registry, PluginType
        connectors = get_registry().list_by_type(PluginType.CONNECTOR)
        for m in connectors:
            assert m.plugin_type == PluginType.CONNECTOR

    def test_connector_stream_default_batches_fetch(self):
        """Default stream() implementation calls fetch() and batches results."""
        from cloudsense.sdk.plugin_sdk import ConnectorPlugin, FocusRecord

        class BatchConnector(ConnectorPlugin):
            name = "batch-stream-test"
            description = "Batch stream test"
            async def fetch(self, start_date, end_date, account_id=None):
                # Return 5 records
                return [
                    FocusRecord(
                        provider_name="aws",
                        billing_account_id="123",
                        billing_period_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                        billing_period_end=datetime(2024, 2, 1, tzinfo=timezone.utc),
                        charge_period_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                        charge_period_end=datetime(2024, 1, 2, tzinfo=timezone.utc),
                        service_name="EC2",
                        billed_cost=Decimal("10"),
                        effective_cost=Decimal("10"),
                        list_cost=Decimal("12"),
                    )
                    for _ in range(5)
                ]

        connector = BatchConnector()
        batches = []
        async def collect():
            async for batch in connector.stream("2024-01-01", "2024-02-01", batch_size=2):
                batches.append(batch)

        asyncio.get_event_loop().run_until_complete(collect())
        assert len(batches) == 3        # ceil(5/2) = 3 batches
        assert len(batches[0]) == 2
        assert len(batches[2]) == 1


class TestPluginRegistry:

    def test_register_overwrites_existing(self):
        from cloudsense.sdk.plugin_sdk import ConnectorPlugin, PluginType, get_registry

        class OverwriteConnector(ConnectorPlugin):
            name = "overwrite-test-v1"
            description = "v1"
            async def fetch(self, *a, **kw): return []

        class OverwriteConnectorV2(ConnectorPlugin):
            name = "overwrite-test-v1"   # Same name — should overwrite
            version = "2.0.0"
            description = "v2"
            async def fetch(self, *a, **kw): return []

        registry = get_registry()
        found    = registry.get(PluginType.CONNECTOR, "overwrite-test-v1")
        assert found is OverwriteConnectorV2

    def test_explicit_register_plugin(self):
        from cloudsense.sdk.plugin_sdk import (
            ConnectorPlugin, PluginType, get_registry, register_plugin
        )

        class ExplicitConnector(ConnectorPlugin):
            name = ""   # Empty name — won't auto-register
            description = "Explicit registration test"
            async def fetch(self, *a, **kw): return []

        ExplicitConnector.name = "explicit-register-test"
        register_plugin(ExplicitConnector)
        found = get_registry().get(PluginType.CONNECTOR, "explicit-register-test")
        assert found is ExplicitConnector


# ─────────────────────────────────────────────────────────────
# Marketplace tests
# ─────────────────────────────────────────────────────────────

class TestMarketplaceRegistry:

    def test_list_all_returns_builtin_plugins(self):
        from cloudsense.sdk.marketplace import MarketplaceRegistry
        reg     = MarketplaceRegistry()
        plugins = reg.list_all()
        assert len(plugins) >= 5
        names = [p.name for p in plugins]
        assert "cloudsense-s3-exporter" in names
        assert "cloudsense-snowflake-connector" in names

    def test_list_by_type_filters(self):
        from cloudsense.sdk.marketplace import MarketplaceRegistry
        from cloudsense.sdk.plugin_sdk import PluginType
        reg        = MarketplaceRegistry()
        connectors = reg.list_all(PluginType.CONNECTOR)
        for p in connectors:
            assert p.plugin_type == PluginType.CONNECTOR

    def test_search_by_name(self):
        from cloudsense.sdk.marketplace import MarketplaceRegistry
        reg     = MarketplaceRegistry()
        results = reg.search("snowflake")
        assert len(results) >= 1
        assert any("snowflake" in r.name for r in results)

    def test_search_by_tag(self):
        from cloudsense.sdk.marketplace import MarketplaceRegistry
        reg     = MarketplaceRegistry()
        results = reg.search("parquet")
        assert any("parquet" in r.tags for r in results)

    def test_search_no_match_returns_empty(self):
        from cloudsense.sdk.marketplace import MarketplaceRegistry
        reg     = MarketplaceRegistry()
        results = reg.search("xyzzy-nonexistent-plugin-12345")
        assert results == []

    def test_get_existing_plugin(self):
        from cloudsense.sdk.marketplace import MarketplaceRegistry
        reg    = MarketplaceRegistry()
        plugin = reg.get("cloudsense-teams-alerter")
        assert plugin is not None
        assert plugin.name    == "cloudsense-teams-alerter"
        assert plugin.verified is True

    def test_get_nonexistent_returns_none(self):
        from cloudsense.sdk.marketplace import MarketplaceRegistry
        reg = MarketplaceRegistry()
        assert reg.get("does-not-exist") is None

    def test_publish_adds_plugin(self):
        from cloudsense.sdk.marketplace import MarketplaceRegistry, PublishRequest
        from cloudsense.sdk.plugin_sdk import PluginType
        reg = MarketplaceRegistry()
        req = PublishRequest(
            name="test-publish-plugin",
            version="1.0.0",
            description="Published during unit test",
            plugin_type=PluginType.CONNECTOR,
            pypi_package="cloudsense-test-publish",
        )
        plugin = reg.publish(req, publisher="tester@example.com")
        assert plugin.name    == "test-publish-plugin"
        assert plugin.verified is False
        assert plugin.install_command == "pip install cloudsense-test-publish"
        assert reg.get("test-publish-plugin") is not None

    def test_increment_downloads(self):
        from cloudsense.sdk.marketplace import MarketplaceRegistry
        reg      = MarketplaceRegistry()
        before   = reg.get("cloudsense-s3-exporter").downloads_total
        reg.increment_downloads("cloudsense-s3-exporter")
        after    = reg.get("cloudsense-s3-exporter").downloads_total
        assert after == before + 1

    def test_sorted_by_downloads_descending(self):
        from cloudsense.sdk.marketplace import MarketplaceRegistry
        reg     = MarketplaceRegistry()
        plugins = reg.list_all()
        counts  = [p.downloads_total for p in plugins]
        assert counts == sorted(counts, reverse=True)


class TestPluginInstaller:

    def test_install_unknown_plugin_returns_failed(self):
        from cloudsense.sdk.marketplace import PluginInstaller
        installer = PluginInstaller()
        result    = asyncio.get_event_loop().run_until_complete(
            installer.install("completely-unknown-plugin-xyz")
        )
        assert result.status == "failed"
        assert "not found" in result.message

    def test_install_already_loaded_returns_already_installed(self):
        from cloudsense.sdk.marketplace import PluginInstaller, _marketplace_store
        from cloudsense.sdk.plugin_sdk import (
            ConnectorPlugin, PluginType, get_registry
        )

        # Register a mock plugin in the SDK registry to simulate already-loaded
        class AlreadyLoadedConnector(ConnectorPlugin):
            name    = "cloudsense-s3-exporter"   # matches a marketplace entry
            version = "2.1.0"
            description = "Already loaded"
            async def fetch(self, *a, **kw): return []

        installer = PluginInstaller()
        result    = asyncio.get_event_loop().run_until_complete(
            installer.install("cloudsense-s3-exporter")
        )
        assert result.status == "already_installed"

    @patch("subprocess.run")
    def test_install_success_calls_pip(self, mock_run):
        from cloudsense.sdk.marketplace import PluginInstaller, _marketplace_store
        from cloudsense.sdk.plugin_sdk import PluginType

        mock_run.return_value = MagicMock(returncode=0, stderr="")

        # Make sure this plugin is NOT in SDK registry
        # Use a fresh plugin name not yet registered
        import sys
        installer = PluginInstaller()
        result    = asyncio.get_event_loop().run_until_complete(
            installer.install("cloudsense-databricks-connector")
        )
        # Should have called pip install
        assert mock_run.called
        cmd = mock_run.call_args[0][0]
        assert "pip" in cmd
        assert "install" in cmd

    @patch("subprocess.run")
    def test_install_pip_failure_returns_failed(self, mock_run):
        from cloudsense.sdk.marketplace import PluginInstaller

        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="ERROR: Could not find a version that satisfies the requirement"
        )

        installer = PluginInstaller()
        result    = asyncio.get_event_loop().run_until_complete(
            installer.install("cloudsense-teams-alerter")
        )
        assert result.status == "failed"


# ─────────────────────────────────────────────────────────────
# Audit Exporter tests
# ─────────────────────────────────────────────────────────────

class TestAuditEvent:

    def test_construction_sets_all_fields(self):
        from cloudsense.audit.exporter import AuditEvent
        evt = AuditEvent(
            event_type="action.executed",
            actor_id="user-123",
            actor_type="user",
            tenant_slug="acme",
            resource_id="i-abc123",
            resource_type="ec2:instance",
            provider="aws",
            outcome="success",
            severity="info",
            details={"action": "stop_instance"},
            ip_address="1.2.3.4",
        )
        assert evt.event_type == "action.executed"
        assert evt.outcome    == "success"
        assert evt.details    == {"action": "stop_instance"}

    def test_event_id_auto_generated(self):
        from cloudsense.audit.exporter import AuditEvent
        e1 = AuditEvent(event_type="test")
        e2 = AuditEvent(event_type="test")
        assert e1.event_id != e2.event_id

    def test_event_is_immutable(self):
        from cloudsense.audit.exporter import AuditEvent
        evt = AuditEvent(event_type="test.event")
        with pytest.raises(AttributeError):
            evt.event_type = "mutated"

    def test_to_dict_contains_all_required_keys(self):
        from cloudsense.audit.exporter import AuditEvent
        evt  = AuditEvent(event_type="insight.approved", actor_id="alice", provider="aws")
        data = evt.to_dict()
        for key in ("event_id", "event_type", "actor_id", "actor_type",
                    "tenant_slug", "provider", "outcome", "severity",
                    "details", "timestamp", "source"):
            assert key in data, f"Missing key: {key}"
        assert data["source"]     == "cloudsense"
        assert data["event_type"] == "insight.approved"


class TestAuditExporterJSONL:

    def _make_events(self, count: int = 5):
        from cloudsense.audit.exporter import AuditEvent
        return [
            AuditEvent(
                event_type="action.executed",
                actor_id=f"user-{i}",
                actor_type="user",
                provider="aws",
                outcome="success",
            )
            for i in range(count)
        ]

    def test_export_jsonl_creates_file(self):
        from cloudsense.audit.exporter import AuditExporter
        events = self._make_events(3)
        with tempfile.TemporaryDirectory() as tmpdir:
            path   = Path(tmpdir) / "audit.jsonl"
            result = asyncio.get_event_loop().run_until_complete(
                AuditExporter().export_to_jsonl(events, path=path)
            )
            assert result["sent"]       == 3
            assert result["destination"] == "jsonl"
            assert path.exists()
            lines = path.read_text().strip().split("\n")
            assert len(lines) == 3
            for line in lines:
                data = json.loads(line)
                assert "event_id"   in data
                assert "event_type" in data

    def test_export_jsonl_append_mode(self):
        from cloudsense.audit.exporter import AuditExporter
        with tempfile.TemporaryDirectory() as tmpdir:
            path   = Path(tmpdir) / "audit.jsonl"
            events = self._make_events(2)
            loop   = asyncio.get_event_loop()
            loop.run_until_complete(AuditExporter().export_to_jsonl(events, path=path, append=True))
            loop.run_until_complete(AuditExporter().export_to_jsonl(events, path=path, append=True))
            lines = path.read_text().strip().split("\n")
            assert len(lines) == 4    # 2 + 2 appended

    def test_export_jsonl_compressed(self):
        from cloudsense.audit.exporter import AuditExporter
        events = self._make_events(4)
        with tempfile.TemporaryDirectory() as tmpdir:
            path   = Path(tmpdir) / "audit.jsonl"
            result = asyncio.get_event_loop().run_until_complete(
                AuditExporter().export_to_jsonl(events, path=path, compress=True)
            )
            gz_path = Path(result["path"])
            assert gz_path.suffix == ".gz"
            raw   = gzip.decompress(gz_path.read_bytes()).decode("utf-8")
            lines = raw.strip().split("\n")
            assert len(lines) == 4

    def test_export_all_jsonl_destination(self):
        from cloudsense.audit.exporter import AuditExporter
        events = self._make_events(3)
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["AUDIT_JSONL_PATH"] = str(Path(tmpdir) / "audit.jsonl")
            result = asyncio.get_event_loop().run_until_complete(
                AuditExporter().export_all(events, destinations=["jsonl"])
            )
            assert result["total_events"]    == 3
            assert "jsonl" in result["results"]
            assert result["results"]["jsonl"]["sent"] == 3


class TestAuditExporterSplunk:

    def _make_events(self):
        from cloudsense.audit.exporter import AuditEvent
        return [AuditEvent(event_type="user.login", actor_id="alice", outcome="success")]

    @patch("httpx.AsyncClient.post")
    def test_splunk_sends_correct_format(self, mock_post):
        from cloudsense.audit.exporter import AuditExporter

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        events   = self._make_events()
        exporter = AuditExporter()
        exporter._http = MagicMock()
        exporter._http.post = AsyncMock(return_value=mock_resp)

        result = asyncio.get_event_loop().run_until_complete(
            exporter.export_to_splunk(
                events,
                hec_url="http://splunk:8088/services/collector/event",
                hec_token="test-token",
            )
        )
        assert result["destination"] == "splunk"
        assert result["sent"]        == 1
        assert result["errors"]      == 0

        call_kwargs = exporter._http.post.call_args
        assert "Authorization" in call_kwargs[1]["headers"]
        assert "Splunk test-token" in call_kwargs[1]["headers"]["Authorization"]
        # Verify Splunk HEC format
        body = json.loads(call_kwargs[1]["content"])
        assert "time"       in body
        assert "event"      in body
        assert "sourcetype" in body
        assert body["sourcetype"] == "cloudsense:audit"

    def test_splunk_raises_without_credentials(self):
        from cloudsense.audit.exporter import AuditExporter
        exporter = AuditExporter()
        with pytest.raises(ValueError, match="Splunk HEC URL"):
            asyncio.get_event_loop().run_until_complete(
                exporter.export_to_splunk(self._make_events())
            )


class TestAuditExporterDatadog:

    def _make_events(self):
        from cloudsense.audit.exporter import AuditEvent
        return [
            AuditEvent(event_type="insight.approved", actor_id="bob", provider="azure")
        ]

    @patch("httpx.AsyncClient.post")
    def test_datadog_sends_log_batch(self, _):
        from cloudsense.audit.exporter import AuditExporter

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        events   = self._make_events()
        exporter = AuditExporter()
        exporter._http = MagicMock()
        exporter._http.post = AsyncMock(return_value=mock_resp)

        result = asyncio.get_event_loop().run_until_complete(
            exporter.export_to_datadog(events, api_key="test-dd-key")
        )
        assert result["destination"] == "datadog"
        assert result["sent"]        == 1

        call_args = exporter._http.post.call_args
        assert "DD-API-KEY" in call_args[1]["headers"]
        body = json.loads(call_args[1]["content"])
        assert isinstance(body, list)
        assert body[0]["ddsource"] == "cloudsense"
        assert "event_type" in body[0]["ddtags"]

    def test_datadog_raises_without_api_key(self):
        from cloudsense.audit.exporter import AuditExporter
        exporter = AuditExporter()
        with pytest.raises(ValueError, match="Datadog API key"):
            asyncio.get_event_loop().run_until_complete(
                exporter.export_to_datadog(self._make_events())
            )
