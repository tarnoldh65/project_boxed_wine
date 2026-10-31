from typing import Any

import pytest
from httpx2 import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import MakeUser, audit_actions

DR_TOKENS = "/api/tenants/detect-respond/tokens"
IT_TOKENS = "/api/tenants/insider-threat/tokens"
NEW_TOKEN = {"name": "siem", "scopes": ["cases:read"], "expires_in_days": 30}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_admin_manages_tokens(
    make_user: MakeUser, client: AsyncClient, owner: AsyncEngine
) -> None:
    admin = await make_user({"detect-respond": ["admin"]})

    created = await admin.client.post(DR_TOKENS, json=NEW_TOKEN)

    assert created.status_code == 201
    body = created.json()
    assert body["token"].startswith("cmt_")
    assert body["prefix"] == body["token"][:12]
    assert body["scopes"] == ["cases:read"]
    async with owner.connect() as conn:
        stored = await conn.scalar(
            text("SELECT token_hash FROM api_tokens WHERE id = :id"), {"id": body["id"]}
        )
    assert stored is not None
    assert body["token"] not in stored

    listed = (await admin.client.get(DR_TOKENS)).json()
    assert [t["id"] for t in listed] == [body["id"]]
    assert "token" not in listed[0]

    # The token authenticates, but only with its own scopes.
    assert (await client.get(DR_TOKENS, headers=bearer(body["token"]))).status_code == 403

    assert (await admin.client.delete(f"{DR_TOKENS}/{body['id']}")).status_code == 204
    assert (await client.get(DR_TOKENS, headers=bearer(body["token"]))).status_code == 401

    assert await audit_actions(owner, f"user:{admin.id}") == [
        "token.create",
        "token.list",
        "token.revoke",
    ]
    assert await audit_actions(owner, f"token:{body['id']}") == ["access.denied"]


async def create_token(make_user: MakeUser, url: str = DR_TOKENS) -> dict[str, Any]:
    admin = await make_user({url.split("/")[3]: ["admin"]})
    response = await admin.client.post(url, json=NEW_TOKEN)
    return response.json()


async def test_token_is_bound_to_its_tenant(
    make_user: MakeUser, client: AsyncClient, owner: AsyncEngine
) -> None:
    token = await create_token(make_user)

    response = await client.get(IT_TOKENS, headers=bearer(token["token"]))

    assert response.status_code == 403
    assert await audit_actions(owner, f"token:{token['id']}") == ["access.denied"]


async def test_token_cannot_use_user_endpoints(make_user: MakeUser, client: AsyncClient) -> None:
    token = await create_token(make_user)
    assert (await client.get("/api/me", headers=bearer(token["token"]))).status_code == 403


async def test_expired_token_is_rejected(
    make_user: MakeUser, client: AsyncClient, owner: AsyncEngine
) -> None:
    token = await create_token(make_user)
    async with owner.begin() as conn:
        await conn.execute(
            text("UPDATE api_tokens SET expires_at = now() - interval '1 second' WHERE id = :id"),
            {"id": token["id"]},
        )
    assert (await client.get(DR_TOKENS, headers=bearer(token["token"]))).status_code == 401


async def test_unknown_token_is_rejected(client: AsyncClient) -> None:
    assert (await client.get(DR_TOKENS, headers=bearer("cmt_unknown"))).status_code == 401


async def test_tokens_are_listed_per_tenant(make_user: MakeUser) -> None:
    token = await create_token(make_user, IT_TOKENS)
    admin = await make_user({"detect-respond": ["admin"]})

    listed = (await admin.client.get(DR_TOKENS)).json()
    assert token["id"] not in [t["id"] for t in listed]
    assert (await admin.client.delete(f"{DR_TOKENS}/{token['id']}")).status_code == 404


async def test_non_admin_cannot_manage_tokens(make_user: MakeUser, owner: AsyncEngine) -> None:
    analyst = await make_user({"detect-respond": ["analyst"]})

    assert (await analyst.client.get(DR_TOKENS)).status_code == 403
    assert (await analyst.client.post(DR_TOKENS, json=NEW_TOKEN)).status_code == 403
    assert await audit_actions(owner, f"user:{analyst.id}") == ["access.denied", "access.denied"]


@pytest.mark.parametrize(
    "body",
    [
        NEW_TOKEN | {"scopes": []},
        NEW_TOKEN | {"scopes": ["tokens:manage"]},
        NEW_TOKEN | {"expires_in_days": 0},
        NEW_TOKEN | {"expires_in_days": 366},
        NEW_TOKEN | {"name": ""},
    ],
)
async def test_invalid_token_requests(make_user: MakeUser, body: dict[str, Any]) -> None:
    admin = await make_user({"detect-respond": ["admin"]})
    assert (await admin.client.post(DR_TOKENS, json=body)).status_code == 422
