
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.mark.asyncio
async def test_healthz():
    """
    Smoke test: the app should start and return a health response.
    Doesn't require auth.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/healthz")
    # 200 = all healthy, 503 = degraded but running
    assert response.status_code in (200, 503)
    assert "status" in response.json()


@pytest.mark.asyncio
async def test_docs_available():
    """Swagger UI should be available at /docs."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/docs")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_protected_route_requires_auth():
    """GET /projects without a token should return 403 (HTTPBearer returns 403 on missing token)."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/projects")
    assert response.status_code == 403
