"""ClickHouse OLAP Client."""
from __future__ import annotations
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
import structlog
from clickhouse_driver import Client as SyncClient
from clickhouse_driver.asyncio import Client as AsyncClient
from cloudsense.sdk.focus_schema import FocusRecord

logger = structlog.get_logger()

BILLING_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS focus_billing (
    billing_account_id LowCardinality(String),
    billing_period_start Date,
    billing_period_end Date,
    charge_period_start DateTime,
    charge_period_end DateTime,
    resource_id String,
    resource_name String,
    resource_type LowCardinality(String),
    service_name LowCardinality(String),
    service_category LowCardinality(String),
    region_id LowCardinality(String),
    region_name LowCardinality(String),
    availability_zone LowCardinality(String),
    list_cost Float64,
    effective_cost Float64,
    amortized_cost Float64 DEFAULT 0,
    usage_quantity Float64,
    usage_unit LowCardinality(String),
    charge_category LowCardinality(String),
    charge_subcategory LowCardinality(String),
    pricing_category LowCardinality(String),
    commitment_discount_id String,
    tags Map(String, String),
    provider LowCardinality(String),
    provider_account_id LowCardinality(String),
    billing_currency LowCardinality(String) DEFAULT 'USD',
    ingested_at DateTime DEFAULT now()
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(billing_period_start)
ORDER BY (provider, billing_account_id, service_name, billing_period_start)
TTL billing_period_start + INTERVAL 3 YEAR
SETTINGS index_granularity = 8192;
"""

DAILY_COST_MV_DDL = """
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_daily_costs
ENGINE = SummingMergeTree()
PARTITION BY toYYYYMM(day)
ORDER BY (provider, billing_account_id, service_name, day)
AS SELECT
    provider,
    billing_account_id,
    service_name,
    service_category,
    region_id,
    toDate(charge_period_start) AS day,
    sum(effective_cost) AS total_effective_cost,
    sum(list_cost) AS total_list_cost,
    sum(usage_quantity) AS total_usage_quantity,
    count() AS row_count
FROM focus_billing
GROUP BY provider, billing_account_id, service_name, service_category, region_id, day;
"""

class ClickHouseClient:
    def __init__(self, host: str = "localhost", port: int = 9000, database: str = "cloudsense",
                 user: str = "default", password: str = "", pool_size: int = 10) -> None:
        self.host = host; self.port = port; self.database = database
        self.user = user; self.password = password; self.pool_size = pool_size
        self._client = None

    async def connect(self) -> None:
        self._client = AsyncClient(host=self.host, port=self.port, database=self.database,
                                   user=self.user, password=self.password)
        logger.info("clickhouse_connected", host=self.host, database=self.database)

    async def init_schema(self) -> None:
        if not self._client: raise RuntimeError("Client not connected")
        await self._client.execute(BILLING_TABLE_DDL)
        await self._client.execute(DAILY_COST_MV_DDL)
        logger.info("clickhouse_schema_initialized")

    async def insert_focus_records(self, records: list[FocusRecord]) -> int:
        if not self._client: raise RuntimeError("Client not connected")
        if not records: return 0
        rows = [r.to_clickhouse_row() for r in records]
        for row in rows:
            if isinstance(row["tags"], dict):
                row["tags"] = json.dumps(row["tags"])
        await self._client.execute("INSERT INTO focus_billing VALUES", rows, types_check=True)
        logger.debug("clickhouse_insert", count=len(rows))
        return len(rows)

    async def query_cost_overview(self, provider: str | None = None,
                                  start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
        if not self._client: raise RuntimeError("Client not connected")
        conditions = ["1=1"]; params: dict[str, Any] = {}
        if provider: conditions.append("provider = %(provider)s"); params["provider"] = provider
        if start_date: conditions.append("billing_period_start >= %(start_date)s"); params["start_date"] = start_date
        if end_date: conditions.append("billing_period_end <= %(end_date)s"); params["end_date"] = end_date
        where_clause = " AND ".join(conditions)
        query = f"""
        SELECT provider, billing_account_id, service_name,
               sum(effective_cost) AS total_cost,
               sum(list_cost - effective_cost) AS total_savings,
               sum(usage_quantity) AS total_usage,
               count() AS record_count
        FROM focus_billing WHERE {where_clause}
        GROUP BY provider, billing_account_id, service_name
        ORDER BY total_cost DESC
        """
        result = await self._client.execute(query, params, with_column_types=True)
        columns = [c[0] for c in result[1]]
        return [dict(zip(columns, row)) for row in result[0]]

    async def query_daily_trend(self, provider: str | None = None, days: int = 30) -> list[dict[str, Any]]:
        if not self._client: raise RuntimeError("Client not connected")
        params: dict[str, Any] = {"days": days}
        provider_filter = "AND provider = %(provider)s" if provider else ""
        if provider: params["provider"] = provider
        query = f"""
        SELECT day, provider, sum(total_effective_cost) AS cost, sum(total_usage_quantity) AS usage
        FROM mv_daily_costs WHERE day >= today() - INTERVAL %(days)s DAY {provider_filter}
        GROUP BY day, provider ORDER BY day ASC
        """
        result = await self._client.execute(query, params, with_column_types=True)
        columns = [c[0] for c in result[1]]
        return [dict(zip(columns, row)) for row in result[0]]

    async def query_top_services(self, provider: str | None = None, limit: int = 10, days: int = 30) -> list[dict[str, Any]]:
        if not self._client: raise RuntimeError("Client not connected")
        params: dict[str, Any] = {"limit": limit, "days": days}
        provider_filter = "AND provider = %(provider)s" if provider else ""
        if provider: params["provider"] = provider
        query = f"""
        SELECT service_name, sum(total_effective_cost) AS total_cost, sum(total_list_cost) AS total_list_cost
        FROM mv_daily_costs WHERE day >= today() - INTERVAL %(days)s DAY {provider_filter}
        GROUP BY service_name ORDER BY total_cost DESC LIMIT %(limit)s
        """
        result = await self._client.execute(query, params, with_column_types=True)
        columns = [c[0] for c in result[1]]
        return [dict(zip(columns, row)) for row in result[0]]

    async def close(self) -> None:
        if self._client:
            await self._client.disconnect(); self._client = None
            logger.info("clickhouse_disconnected")

    @asynccontextmanager
    async def session(self) -> AsyncIterator[ClickHouseClient]:
        await self.connect()
        try: yield self
        finally: await self.close()
