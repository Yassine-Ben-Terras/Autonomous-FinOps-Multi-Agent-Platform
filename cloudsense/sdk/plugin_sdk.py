"""
CloudSense Phase 5.3 — Plugin SDK
===================================
The public Python SDK that community developers use to build
custom CloudSense plugins: connectors, agents, exporters, and alerters.

Published on PyPI as: ``cloudsense-sdk``

Design goals
------------
- One base class per plugin type (Connector, Agent, Exporter, Alerter)
- Strict interface contracts via abstract methods
- Automatic registration with the plugin marketplace on import
- Pydantic models for all data structures (plugin authors get validation for free)
- Zero CloudSense internals exposed — only the public surface

Plugin types
------------
ConnectorPlugin  — ingest billing data from a custom source → FOCUS records
AgentPlugin      — add a specialist analysis agent to the supervisor DAG
ExporterPlugin   — export FOCUS data to a new destination / format
AlerterPlugin    — send alerts/notifications to a new channel

Quick-start example
-------------------
    # my_plugin.py
    from cloudsense_sdk import ConnectorPlugin, FocusRecord, register_plugin

    class MyConnector(ConnectorPlugin):
        name        = "my-cloud"
        version     = "1.0.0"
        description = "Fetches billing from MyCloud provider"

        async def fetch(self, start_date: str, end_date: str) -> list[FocusRecord]:
            # ... call your cloud API ...
            return [FocusRecord(...)]

    register_plugin(MyConnector)

    # cloudsense.yaml
    plugins:
      - path: my_plugin.MyConnector
"""

from __future__ import annotations

import abc
import importlib
import inspect
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any, AsyncIterator

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── FOCUS record (public re-export) ──────────────────────────────────────────

class FocusRecord(BaseModel):
    """
    FOCUS 1.0 billing record — the common currency for all CloudSense data.
    Plugin authors produce and consume this model.
    Full spec: https://focus.finops.org/specification/
    """
    model_config = {"populate_by_name": True}

    provider_name:          str
    billing_account_id:     str
    billing_account_name:   str                  = ""
    sub_account_id:         str | None           = None
    billing_period_start:   datetime
    billing_period_end:     datetime
    charge_period_start:    datetime
    charge_period_end:      datetime
    charge_category:        str                  = "Usage"
    service_name:           str
    service_category:       str                  = ""
    region_id:              str | None           = None
    resource_id:            str | None           = None
    resource_name:          str | None           = None
    resource_type:          str | None           = None
    billed_cost:            Decimal
    effective_cost:         Decimal
    list_cost:              Decimal
    billing_currency:       str                  = "USD"
    usage_quantity:         Decimal | None       = None
    usage_unit:             str | None           = None
    pricing_category:       str                  = "On-Demand"
    commitment_discount_id: str | None           = None
    commitment_discount_type: str               = "None"
    tags:                   dict[str, str]       = Field(default_factory=dict)


# ── Plugin metadata ───────────────────────────────────────────────────────────

class PluginType(StrEnum):
    CONNECTOR = "connector"
    AGENT     = "agent"
    EXPORTER  = "exporter"
    ALERTER   = "alerter"


@dataclass
class PluginManifest:
    """Metadata every plugin must declare."""
    name:         str
    version:      str
    description:  str
    author:       str         = ""
    author_email: str         = ""
    homepage:     str         = ""
    plugin_type:  PluginType  = PluginType.CONNECTOR
    tags:         list[str]   = field(default_factory=list)
    requires:     list[str]   = field(default_factory=list)  # pip dependencies


# ── Base classes ──────────────────────────────────────────────────────────────

class BasePlugin(abc.ABC):
    """Base class for all CloudSense plugins."""

    #: Plugin metadata — must be overridden by subclass
    name:        str = ""
    version:     str = "0.1.0"
    description: str = ""
    author:      str = ""
    plugin_type: PluginType = PluginType.CONNECTOR

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Auto-register concrete (non-abstract) subclasses
        if not inspect.isabstract(cls) and cls.name:
            _registry.register(cls)

    @classmethod
    def manifest(cls) -> PluginManifest:
        return PluginManifest(
            name=cls.name,
            version=cls.version,
            description=cls.description,
            author=cls.author,
            plugin_type=cls.plugin_type,
        )

    async def on_load(self) -> None:
        """Called once when the plugin is loaded. Override for init work."""

    async def on_unload(self) -> None:
        """Called once when the plugin is unloaded. Override for cleanup."""


class ConnectorPlugin(BasePlugin, abc.ABC):
    """
    Base class for billing data connectors.

    Implement ``fetch()`` to return FOCUS records from your cloud or SaaS source.
    CloudSense calls this on the ingestion schedule and streams results into
    ClickHouse via the FOCUS normalisation pipeline.
    """

    plugin_type: PluginType = PluginType.CONNECTOR

    @abc.abstractmethod
    async def fetch(
        self,
        start_date: str,
        end_date: str,
        account_id: str | None = None,
    ) -> list[FocusRecord]:
        """
        Fetch billing records for the given date range.

        Parameters
        ----------
        start_date : ISO date string, inclusive (e.g. "2024-01-01")
        end_date   : ISO date string, exclusive  (e.g. "2024-02-01")
        account_id : optional scope filter

        Returns
        -------
        list[FocusRecord]  — normalised FOCUS 1.0 billing records
        """
        ...

    async def stream(
        self,
        start_date: str,
        end_date: str,
        batch_size: int = 1000,
    ) -> AsyncIterator[list[FocusRecord]]:
        """
        Streaming variant of fetch — override for large datasets.
        Default implementation calls fetch() and batches the result.
        """
        records = await self.fetch(start_date, end_date)
        for i in range(0, len(records), batch_size):
            yield records[i:i + batch_size]


class AgentPlugin(BasePlugin, abc.ABC):
    """
    Base class for specialist analysis agents.

    Implement ``analyze()`` to return CostInsight objects.
    CloudSense's supervisor DAG will call this after the built-in
    AWS/Azure/GCP agents and merge the results.
    """

    plugin_type: PluginType = PluginType.AGENT

    @abc.abstractmethod
    async def analyze(
        self,
        billing_data: list[FocusRecord],
        time_range_days: int = 30,
        **context: Any,
    ) -> list[dict[str, Any]]:
        """
        Analyse billing data and return insights.

        Returns list of dicts matching CostInsight schema:
          insight_id, agent, provider, severity, title, description,
          resource_ids, projected_monthly_savings, confidence_score,
          recommendation, action_type, risk_level, tags
        """
        ...


class ExporterPlugin(BasePlugin, abc.ABC):
    """
    Base class for data exporters.

    Implement ``export()`` to write FOCUS data to a new destination —
    S3, GCS, a custom BI tool, a data warehouse, etc.
    """

    plugin_type: PluginType = PluginType.EXPORTER

    @abc.abstractmethod
    async def export(
        self,
        records: list[FocusRecord],
        start_date: str,
        end_date: str,
        destination: str,
        **options: Any,
    ) -> dict[str, Any]:
        """
        Export FOCUS records to a destination.

        Returns a result dict:
          status, records_exported, destination, size_bytes, url (optional)
        """
        ...


class AlerterPlugin(BasePlugin, abc.ABC):
    """
    Base class for alert/notification channels.

    Implement ``send()`` to deliver cost alerts to a custom channel —
    Teams, PagerDuty, webhook, SMS, etc.
    """

    plugin_type: PluginType = PluginType.ALERTER

    @abc.abstractmethod
    async def send(
        self,
        title: str,
        message: str,
        severity: str = "warning",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Send an alert.

        Returns a result dict: status, channel, message_id (optional)
        """
        ...


# ── Plugin registry ───────────────────────────────────────────────────────────

class PluginRegistry:
    """
    In-process registry of all loaded plugins.
    Thread-safe for reads; writes happen only at load time.
    """

    def __init__(self) -> None:
        self._plugins: dict[str, type[BasePlugin]] = {}

    def register(self, plugin_cls: type[BasePlugin]) -> None:
        key = f"{plugin_cls.plugin_type}:{plugin_cls.name}"
        if key in self._plugins:
            logger.warning(
                "Plugin already registered — overwriting: %s", key
            )
        self._plugins[key] = plugin_cls
        logger.info("Plugin registered: %s v%s", plugin_cls.name, plugin_cls.version)

    def get(self, plugin_type: PluginType, name: str) -> type[BasePlugin] | None:
        return self._plugins.get(f"{plugin_type}:{name}")

    def list_all(self) -> list[PluginManifest]:
        return [cls.manifest() for cls in self._plugins.values()]

    def list_by_type(self, plugin_type: PluginType) -> list[PluginManifest]:
        return [
            cls.manifest()
            for key, cls in self._plugins.items()
            if key.startswith(f"{plugin_type}:")
        ]

    def load_from_path(self, dotted_path: str) -> type[BasePlugin]:
        """
        Dynamically load a plugin class from a dotted import path.

        Example: ``registry.load_from_path("my_package.MyConnector")``
        """
        module_path, class_name = dotted_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        cls    = getattr(module, class_name)
        if not issubclass(cls, BasePlugin):
            raise TypeError(f"{dotted_path} is not a CloudSense plugin")
        self.register(cls)
        return cls


# ── Global registry singleton ─────────────────────────────────────────────────
_registry = PluginRegistry()


def register_plugin(plugin_cls: type[BasePlugin]) -> None:
    """Explicitly register a plugin (use when auto-registration doesn't fire)."""
    _registry.register(plugin_cls)


def get_registry() -> PluginRegistry:
    """Return the global plugin registry."""
    return _registry


__all__ = [
    # Base classes
    "BasePlugin",
    "ConnectorPlugin",
    "AgentPlugin",
    "ExporterPlugin",
    "AlerterPlugin",
    # Data models
    "FocusRecord",
    "PluginManifest",
    "PluginType",
    # Registry
    "PluginRegistry",
    "get_registry",
    "register_plugin",
]
