"""
CloudSense Phase 5.3 — Plugin Marketplace
==========================================
The marketplace is the community hub for discovering, installing,
and managing CloudSense plugins.

Architecture
------------
- MarketplaceRegistry  : server-side registry of published plugins
- MarketplaceClient    : API client used by the CloudSense CLI / admin UI
- MarketplaceRouter    : FastAPI router at /api/v1/marketplace/*

Plugin lifecycle
----------------
  Author publishes → marketplace lists it → user installs via CLI
  → plugin loaded at startup → contributes to DAG / ingestion / exports

Marketplace endpoints
---------------------
  GET  /marketplace/plugins           List all published plugins
  GET  /marketplace/plugins/{name}    Get plugin details + install command
  POST /marketplace/plugins           Publish a new plugin (auth required)
  GET  /marketplace/categories        Browse by category
  GET  /marketplace/search?q=...      Full-text search over name + description
  POST /marketplace/install           Install a plugin into this CloudSense instance
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, HttpUrl

from cloudsense.auth.deps import require_permission
from cloudsense.auth.models import Permission, TokenClaims
from cloudsense.sdk.plugin_sdk import PluginManifest, PluginType, get_registry

logger = structlog.get_logger()
router = APIRouter(prefix="/marketplace", tags=["Plugin Marketplace (Phase 5.3)"])


# ── Marketplace data models ───────────────────────────────────────────────────

class MarketplacePlugin(BaseModel):
    """A plugin listed in the CloudSense marketplace."""

    name:           str
    version:        str
    description:    str
    author:         str             = ""
    author_email:   str             = ""
    homepage:       str             = ""
    plugin_type:    PluginType
    tags:           list[str]       = Field(default_factory=list)
    requires:       list[str]       = Field(default_factory=list)

    # Marketplace-specific fields
    install_command: str            = ""   # e.g. "pip install cloudsense-plugin-snowflake"
    pypi_package:    str            = ""   # PyPI package name
    github_url:      str            = ""
    docs_url:        str            = ""
    logo_url:        str            = ""

    # Stats
    downloads_total:  int           = 0
    stars:            int           = 0
    verified:         bool          = False   # CloudSense team reviewed

    published_at:    datetime       = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at:      datetime       = Field(default_factory=lambda: datetime.now(timezone.utc))


class PublishRequest(BaseModel):
    name:          str
    version:       str
    description:   str
    author:        str         = ""
    author_email:  str         = ""
    homepage:      str         = ""
    plugin_type:   PluginType
    tags:          list[str]   = Field(default_factory=list)
    requires:      list[str]   = Field(default_factory=list)
    pypi_package:  str         = ""
    github_url:    str         = ""
    docs_url:      str         = ""


class InstallRequest(BaseModel):
    plugin_name: str = Field(..., description="Marketplace plugin name to install")
    version:     str = Field(default="latest")


class InstallResult(BaseModel):
    plugin_name: str
    version:     str
    status:      str                  # installed | already_installed | failed
    message:     str                  = ""
    loaded:      bool                 = False


# ── In-memory marketplace registry ───────────────────────────────────────────
# In production: backed by a Postgres table and optional Redis cache.

_BUILTIN_PLUGINS: list[MarketplacePlugin] = [
    MarketplacePlugin(
        name="cloudsense-snowflake-connector",
        version="1.2.0",
        description=(
            "Ingest Snowflake warehouse billing data as FOCUS records. "
            "Tracks compute credits, storage, and data transfer costs per warehouse."
        ),
        author="CloudSense Community",
        plugin_type=PluginType.CONNECTOR,
        tags=["snowflake", "data-warehouse", "connector"],
        requires=["snowflake-connector-python>=3.0"],
        install_command="pip install cloudsense-snowflake-connector",
        pypi_package="cloudsense-snowflake-connector",
        github_url="https://github.com/cloudsense-community/snowflake-connector",
        downloads_total=1_240,
        stars=34,
        verified=True,
    ),
    MarketplacePlugin(
        name="cloudsense-databricks-connector",
        version="1.0.1",
        description=(
            "Fetch Databricks Unit Economics (DBU) spend from the Databricks Billing API "
            "and map to FOCUS service cost records."
        ),
        author="CloudSense Community",
        plugin_type=PluginType.CONNECTOR,
        tags=["databricks", "spark", "connector"],
        requires=["databricks-sdk>=0.20"],
        install_command="pip install cloudsense-databricks-connector",
        pypi_package="cloudsense-databricks-connector",
        github_url="https://github.com/cloudsense-community/databricks-connector",
        downloads_total=890,
        stars=21,
        verified=True,
    ),
    MarketplacePlugin(
        name="cloudsense-teams-alerter",
        version="1.0.0",
        description=(
            "Send CloudSense cost anomaly alerts and recommendations to "
            "Microsoft Teams channels via incoming webhooks."
        ),
        author="CloudSense Community",
        plugin_type=PluginType.ALERTER,
        tags=["microsoft-teams", "notifications", "alerter"],
        requires=["httpx>=0.27"],
        install_command="pip install cloudsense-teams-alerter",
        pypi_package="cloudsense-teams-alerter",
        downloads_total=670,
        stars=18,
        verified=True,
    ),
    MarketplacePlugin(
        name="cloudsense-s3-exporter",
        version="2.1.0",
        description=(
            "Export FOCUS billing data to Amazon S3 in Parquet, CSV, or JSONL format "
            "on a configurable schedule. Supports partitioning by year/month/provider."
        ),
        author="CloudSense Community",
        plugin_type=PluginType.EXPORTER,
        tags=["s3", "aws", "parquet", "exporter"],
        requires=["boto3>=1.34", "pyarrow>=16.0"],
        install_command="pip install cloudsense-s3-exporter",
        pypi_package="cloudsense-s3-exporter",
        downloads_total=2_105,
        stars=56,
        verified=True,
    ),
    MarketplacePlugin(
        name="cloudsense-rightsizing-agent",
        version="1.3.0",
        description=(
            "Advanced right-sizing agent that uses CloudWatch percentile metrics "
            "and ML-based workload classification to recommend EC2, RDS, and ECS task "
            "size changes with zero-performance-impact confidence scoring."
        ),
        author="CloudSense Community",
        plugin_type=PluginType.AGENT,
        tags=["aws", "rightsizing", "ml", "agent"],
        requires=["scikit-learn>=1.4", "boto3>=1.34"],
        install_command="pip install cloudsense-rightsizing-agent",
        pypi_package="cloudsense-rightsizing-agent",
        github_url="https://github.com/cloudsense-community/rightsizing-agent",
        downloads_total=1_580,
        stars=43,
        verified=True,
    ),
]

_marketplace_store: dict[str, MarketplacePlugin] = {
    p.name: p for p in _BUILTIN_PLUGINS
}


# ── Marketplace registry class ────────────────────────────────────────────────

class MarketplaceRegistry:
    """Server-side plugin catalogue."""

    def list_all(self, plugin_type: PluginType | None = None) -> list[MarketplacePlugin]:
        plugins = list(_marketplace_store.values())
        if plugin_type:
            plugins = [p for p in plugins if p.plugin_type == plugin_type]
        return sorted(plugins, key=lambda p: p.downloads_total, reverse=True)

    def search(self, query: str) -> list[MarketplacePlugin]:
        q = query.lower()
        return [
            p for p in _marketplace_store.values()
            if q in p.name.lower()
            or q in p.description.lower()
            or any(q in t for t in p.tags)
        ]

    def get(self, name: str) -> MarketplacePlugin | None:
        return _marketplace_store.get(name)

    def publish(self, req: PublishRequest, publisher: str) -> MarketplacePlugin:
        plugin = MarketplacePlugin(
            **req.model_dump(),
            install_command=f"pip install {req.pypi_package or req.name}",
            verified=False,
        )
        _marketplace_store[plugin.name] = plugin
        logger.info("marketplace.published", name=plugin.name, publisher=publisher)
        return plugin

    def increment_downloads(self, name: str) -> None:
        if name in _marketplace_store:
            _marketplace_store[name].downloads_total += 1


_registry_instance = MarketplaceRegistry()


# ── Plugin installer ──────────────────────────────────────────────────────────

class PluginInstaller:
    """
    Installs marketplace plugins via pip and loads them into the runtime registry.

    Safety: only installs packages listed in the marketplace (allowlist).
    """

    def __init__(self) -> None:
        self._marketplace = _registry_instance
        self._sdk_registry = get_registry()

    async def install(self, plugin_name: str, version: str = "latest") -> InstallResult:
        plugin_meta = self._marketplace.get(plugin_name)
        if not plugin_meta:
            return InstallResult(
                plugin_name=plugin_name,
                version=version,
                status="failed",
                message=f"Plugin '{plugin_name}' not found in marketplace",
            )

        package = plugin_meta.pypi_package or plugin_meta.name
        pip_spec = package if version == "latest" else f"{package}=={version}"

        logger.info("marketplace.installing", plugin=plugin_name, package=pip_spec)

        # Check if already installed
        existing = self._sdk_registry.get(plugin_meta.plugin_type, plugin_name)
        if existing:
            return InstallResult(
                plugin_name=plugin_name,
                version=existing.version,
                status="already_installed",
                message=f"Plugin '{plugin_name}' v{existing.version} already loaded",
                loaded=True,
            )

        # pip install
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", pip_spec, "--quiet"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr)
        except Exception as exc:
            logger.error("marketplace.install_failed", plugin=plugin_name, error=str(exc))
            return InstallResult(
                plugin_name=plugin_name,
                version=version,
                status="failed",
                message=str(exc),
            )

        self._marketplace.increment_downloads(plugin_name)
        logger.info("marketplace.installed", plugin=plugin_name)
        return InstallResult(
            plugin_name=plugin_name,
            version=version,
            status="installed",
            message=f"Installed '{pip_spec}'. Restart CloudSense to load the plugin.",
            loaded=False,
        )


_installer = PluginInstaller()


# ── API Router ─────────────────────────────────────────────────────────────────

@router.get(
    "/plugins",
    summary="List all marketplace plugins",
    response_model=list[MarketplacePlugin],
)
async def list_plugins(
    plugin_type: PluginType | None = Query(default=None, description="Filter by plugin type"),
    verified:    bool | None       = Query(default=None, description="Show only verified plugins"),
) -> list[MarketplacePlugin]:
    plugins = _registry_instance.list_all(plugin_type)
    if verified is not None:
        plugins = [p for p in plugins if p.verified == verified]
    return plugins


@router.get(
    "/plugins/{plugin_name}",
    summary="Get plugin details",
    response_model=MarketplacePlugin,
)
async def get_plugin(plugin_name: str) -> MarketplacePlugin:
    plugin = _registry_instance.get(plugin_name)
    if not plugin:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_name}' not found")
    return plugin


@router.get(
    "/search",
    summary="Search marketplace plugins",
    response_model=list[MarketplacePlugin],
)
async def search_plugins(
    q: str = Query(..., min_length=2, description="Search query"),
) -> list[MarketplacePlugin]:
    return _registry_instance.search(q)


@router.get(
    "/categories",
    summary="List plugin categories",
)
async def list_categories() -> dict[str, Any]:
    plugins = _registry_instance.list_all()
    categories: dict[str, int] = {}
    for p in plugins:
        categories[p.plugin_type] = categories.get(p.plugin_type, 0) + 1
    return {"categories": categories, "total": len(plugins)}


@router.post(
    "/plugins",
    summary="Publish a plugin to the marketplace",
    status_code=201,
    response_model=MarketplacePlugin,
)
async def publish_plugin(
    req:   PublishRequest,
    token: TokenClaims = Depends(require_permission(Permission.ADMIN)),
) -> MarketplacePlugin:
    return _registry_instance.publish(req, publisher=token.sub)


@router.post(
    "/install",
    summary="Install a marketplace plugin into this CloudSense instance",
    response_model=InstallResult,
)
async def install_plugin(
    req:   InstallRequest,
    token: TokenClaims = Depends(require_permission(Permission.ADMIN)),
) -> InstallResult:
    logger.info(
        "marketplace.install_requested",
        plugin=req.plugin_name,
        version=req.version,
        by=token.sub,
    )
    return await _installer.install(req.plugin_name, req.version)


@router.get(
    "/installed",
    summary="List plugins currently loaded in this instance",
)
async def list_installed() -> dict[str, Any]:
    sdk_registry = get_registry()
    manifests    = sdk_registry.list_all()
    return {
        "installed": [m.__dict__ for m in manifests],
        "count":     len(manifests),
    }
