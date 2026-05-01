"""Shared Agent Tools — ClickHouse query wrappers."""
from __future__ import annotations
from typing import Any
import structlog
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
from cloudsense.services.db.clickhouse import ClickHouseClient

logger = structlog.get_logger()

class ClickHouseQueryInput(BaseModel):
    query_description: str = Field(...)
    sql: str = Field(...)
    params: dict[str, Any] = Field(default_factory=dict)

class ClickHouseQueryTool(BaseTool):
    name: str = "clickhouse_query"
    description: str = "Execute a read-only SQL query against ClickHouse billing data."
    args_schema: type[BaseModel] = ClickHouseQueryInput
    def __init__(self, clickhouse_client: ClickHouseClient) -> None:
        super().__init__()
        self._ch = clickhouse_client
    def _run(self, query_description: str, sql: str, params: dict[str, Any] | None = None) -> str:
        import asyncio
        params = params or {}
        try:
            loop = asyncio.get_event_loop()
            result = loop.run_until_complete(self._ch._client.execute(sql, params, with_column_types=True))
            columns = [c[0] for c in result[1]]
            rows = [dict(zip(columns, row)) for row in result[0]]
            logger.debug("tool_clickhouse_query", desc=query_description, rows=len(rows))
            return f"Query: {query_description}\nResults ({len(rows)} rows):\n{rows[:50]}"
        except Exception as exc:
            logger.error("tool_clickhouse_query_failed", error=str(exc))
            return f"Query failed: {exc}"
    async def _arun(self, *args: Any, **kwargs: Any) -> str:
        return self._run(*args, **kwargs)

class ResourceListInput(BaseModel):
    provider: str = Field(...)
    service_name: str | None = None
    region: str | None = None
    min_monthly_cost: float = Field(10.0)

class ResourceListTool(BaseTool):
    name: str = "list_high_cost_resources"
    description: str = "List resources with monthly cost above a threshold."
    args_schema: type[BaseModel] = ResourceListInput
    def __init__(self, clickhouse_client: ClickHouseClient) -> None:
        super().__init__()
        self._ch = clickhouse_client
    def _run(self, provider: str, service_name: str | None = None, region: str | None = None, min_monthly_cost: float = 10.0) -> str:
        import asyncio
        conditions = ["provider = %(provider)s", "billing_period_start >= today() - INTERVAL 30 DAY"]
        params: dict[str, Any] = {"provider": provider, "min_cost": min_monthly_cost}
        if service_name: conditions.append("service_name = %(service)s"); params["service"] = service_name
        if region: conditions.append("region_id = %(region)s"); params["region"] = region
        where = " AND ".join(conditions)
        sql = f"""SELECT resource_id, resource_name, service_name, region_id, sum(effective_cost) AS monthly_cost, sum(usage_quantity) AS monthly_usage
                  FROM focus_billing WHERE {where} GROUP BY resource_id, resource_name, service_name, region_id
                  HAVING monthly_cost >= %(min_cost)s ORDER BY monthly_cost DESC LIMIT 100"""
        try:
            loop = asyncio.get_event_loop()
            result = loop.run_until_complete(self._ch._client.execute(sql, params, with_column_types=True))
            columns = [c[0] for c in result[1]]
            rows = [dict(zip(columns, row)) for row in result[0]]
            return f"High-cost resources ({len(rows)}):\n{rows}"
        except Exception as exc:
            return f"Error: {exc}"
    async def _arun(self, *args: Any, **kwargs: Any) -> str:
        return self._run(*args, **kwargs)
