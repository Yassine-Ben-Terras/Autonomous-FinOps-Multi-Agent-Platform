"""API integration tests."""
import pytest
from httpx import AsyncClient
from cloudsense.services.api.main import app

@pytest.mark.asyncio
async def test_health_endpoint():
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

@pytest.mark.asyncio
async def test_costs_overview():
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/v1/costs/overview")
        assert response.status_code == 200
        data = response.json()
        assert "period" in data
        assert "summary" in data
