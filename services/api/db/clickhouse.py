"""ClickHouse connection factory for the CloudSense API."""

from __future__ import annotations

import clickhouse_connect
from clickhouse_connect.driver.client import Client

from services.api.config import settings

_client: Client | None = None


def get_clickhouse_client() -> Client:
    """Return a shared ClickHouse client (lazy singleton)."""
    global _client
    if _client is None:
        _client = clickhouse_connect.get_client(
            host=settings.clickhouse_host,
            port=settings.clickhouse_port,
            database=settings.clickhouse_db,
            username=settings.clickhouse_user,
            password=settings.clickhouse_password,
            # Connection pool settings
            connect_timeout=10,
            send_receive_timeout=60,
            compress=True,
        )
    return _client
