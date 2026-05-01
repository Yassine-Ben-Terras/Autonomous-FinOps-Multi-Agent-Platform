"""Abstract base class for cloud cost connectors."""
from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import date
from typing import AsyncIterator
from cloudsense.sdk.focus_schema import FocusBatch

class CostConnector(ABC):
    provider: str
    def __init__(self, connector_id: str, config: dict | None = None) -> None:
        self.connector_id = connector_id
        self.config = config or {}
        self._client = None
    @abstractmethod
    async def health_check(self) -> dict:
        ...
    @abstractmethod
    async def fetch_billing(self, start_date: date, end_date: date) -> AsyncIterator[FocusBatch]:
        ...
    @abstractmethod
    async def get_accounts(self) -> list[dict]:
        ...
    async def close(self) -> None:
        pass
