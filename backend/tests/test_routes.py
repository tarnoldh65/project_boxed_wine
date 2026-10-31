"""Authentication and tenant membership are enforced on every API route."""

from uuid import uuid4

import pytest
from httpx2 import AsyncClient

from app.main import app
from tests.conftest import MakeUser

PUBLIC = {"/api/auth/login", "/api/auth/callback", "/api/auth/logout"}
API_ROUTES = sorted(
    (method.upper(), path)
    for path, operations in app.openapi()["paths"].items()
    if path.startswith("/api") and path not in PUBLIC
    for method in operations
)
TENANT_ROUTES = [(m, p) for m, p in API_ROUTES if p.startswith("/api/tenants/{slug}")]


def fill(path: str) -> str:
    params = {
        "slug": "detect-respond",
        "token_id": str(uuid4()),
        "asset_id": str(uuid4()),
        "number": "1",
        "source": "siem",
        "observable_id": str(uuid4()),
    }
    for name, value in params.items():
        path = path.replace(f"{{{name}}}", value)
    return path


def test_routes_are_discovered() -> None:
    assert ("GET", "/api/me") in API_ROUTES
    assert TENANT_ROUTES


@pytest.mark.parametrize(("method", "path"), API_ROUTES)
async def test_requires_authentication(client: AsyncClient, method: str, path: str) -> None:
    response = await client.request(method, fill(path), json={})
    assert response.status_code == 401


@pytest.mark.parametrize(("method", "path"), TENANT_ROUTES)
async def test_rejects_non_members(make_user: MakeUser, method: str, path: str) -> None:
    outsider = await make_user({"insider-threat": ["admin"]}, platform_admin=True)
    response = await outsider.client.request(method, fill(path), json={})
    assert response.status_code == 403
