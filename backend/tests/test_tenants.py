import secrets

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import MakeUser, audit_actions


async def test_platform_admin_lists_and_creates_tenants(
    make_user: MakeUser, owner: AsyncEngine
) -> None:
    admin = await make_user(platform_admin=True)
    slug = f"subsidiary-{secrets.token_hex(3)}"

    created = await admin.client.post("/api/tenants", json={"slug": slug, "name": "Subsidiary"})
    assert created.status_code == 201
    assert created.json()["slug"] == slug

    slugs = [t["slug"] for t in (await admin.client.get("/api/tenants")).json()]
    assert {"detect-respond", "insider-threat", slug} <= set(slugs)

    duplicate = await admin.client.post("/api/tenants", json={"slug": slug, "name": "Again"})
    assert duplicate.status_code == 409
    assert await audit_actions(owner, f"user:{admin.id}") == ["tenant.create", "tenant.list"]


@pytest.mark.parametrize("slug", ["", "Upper", "trailing-", "-leading", "two--dashes", "a b"])
async def test_tenant_slug_is_validated(make_user: MakeUser, slug: str) -> None:
    admin = await make_user(platform_admin=True)
    response = await admin.client.post("/api/tenants", json={"slug": slug, "name": "X"})
    assert response.status_code == 422


async def test_tenant_admin_is_not_platform_admin(make_user: MakeUser) -> None:
    admin = await make_user({"detect-respond": ["admin"]})
    assert (await admin.client.get("/api/tenants")).status_code == 403
    response = await admin.client.post("/api/tenants", json={"slug": "x", "name": "X"})
    assert response.status_code == 403
