from typing import Any

import pytest
from httpx2 import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import MakeUser, SignedIn, audit_actions, close_case

REVIEW = {
    "root_cause": "MFA was not enforced on the VPN",
    "contributing_factors": "Shared service account",
    "went_well": "Fast containment",
    "went_poorly": "Late legal engagement",
}


@pytest.fixture
async def analyst(make_user: MakeUser, tenant: str) -> SignedIn:
    return await make_user({tenant: ["analyst"]})


def url(tenant: str, number: int, suffix: str = "") -> str:
    return f"/api/tenants/{tenant}/cases/{number}{suffix}"


async def new_case(client: AsyncClient, tenant: str) -> int:
    response = await client.post(
        f"/api/tenants/{tenant}/cases", json={"template": "ddos", "title": "x"}
    )
    assert response.status_code == 201, response.text
    return response.json()["number"]


async def call(client: AsyncClient, method: str, path: str, body: Any, status: int = 200) -> Any:
    response = await client.request(method, path, json=body)
    assert response.status_code == status, response.text
    return response.json()


async def test_a_true_positive_cannot_close_until_the_review_is_complete(
    analyst: SignedIn, tenant: str, owner: AsyncEngine
) -> None:
    number = await new_case(analyst.client, tenant)
    with pytest.raises(AssertionError, match="complete the post-incident review first"):
        await close_case(analyst.client, tenant, number, "true_positive")

    partial = await call(
        analyst.client, "PUT", url(tenant, number, "/pir"), REVIEW | {"went_poorly": "  "}
    )
    assert partial["pir"]["complete"] is False
    assert partial["pir"]["went_poorly"] == ""
    with pytest.raises(AssertionError, match="complete the post-incident review first"):
        await close_case(analyst.client, tenant, number, "true_positive")

    case = await call(analyst.client, "PUT", url(tenant, number, "/pir"), REVIEW)
    assert case["pir"] | {"updated_at": None} == REVIEW | {
        "complete": True,
        "updated_by": f"user:{analyst.id}",
        "updated_at": None,
        "actions": [],
    }
    closed = await close_case(analyst.client, tenant, number, "true_positive")
    assert closed["state"] == "closed"
    assert "pir.update" in await audit_actions(owner, f"user:{analyst.id}")


async def test_false_positives_close_without_a_review(analyst: SignedIn, tenant: str) -> None:
    number = await new_case(analyst.client, tenant)
    closed = await close_case(analyst.client, tenant, number, "false_positive")
    assert closed["pir"]["complete"] is False


async def test_the_gate_follows_the_state_setting(
    make_user: MakeUser, analyst: SignedIn, tenant: str
) -> None:
    admin = await make_user({tenant: ["admin"]})
    states = (await admin.client.get(f"/api/tenants/{tenant}/config")).json()["states"]
    states[-1]["require_pir"] = False
    response = await admin.client.put(f"/api/tenants/{tenant}/config/states", json=states)
    assert response.status_code == 200, response.text

    number = await new_case(analyst.client, tenant)
    closed = await close_case(analyst.client, tenant, number, "true_positive")
    assert closed["state"] == "closed"


async def test_action_items_are_tracked_to_closure(
    analyst: SignedIn, make_user: MakeUser, tenant: str, owner: AsyncEngine
) -> None:
    number = await new_case(analyst.client, tenant)
    actions = url(tenant, number, "/pir/actions")
    stranger = await make_user({"insider-threat": ["analyst"]})
    rejected = await analyst.client.post(
        actions, json={"title": "Enforce MFA", "owner_id": str(stranger.id)}
    )
    assert rejected.status_code == 422
    assert "owner must be a tenant member" in rejected.text

    case = await call(
        analyst.client,
        "POST",
        actions,
        {
            "title": "Enforce MFA",
            "owner_id": str(analyst.id),
            "due_at": "2026-12-01T09:00:00+01:00",
        },
        201,
    )
    action = case["pir"]["actions"][0]
    assert (action["title"], action["owner_id"], action["due_at"], action["done_at"]) == (
        "Enforce MFA", str(analyst.id), "2026-12-01T08:00:00Z", None,
    )  # fmt: skip

    done = await call(analyst.client, "PATCH", f"{actions}/{action['id']}", {"done": True})
    assert done["pir"]["actions"][0]["done_at"] is not None
    again = await call(analyst.client, "PATCH", f"{actions}/{action['id']}", {"done": True})
    assert again["pir"]["actions"][0]["done_at"] == done["pir"]["actions"][0]["done_at"]
    reopened = await call(analyst.client, "PATCH", f"{actions}/{action['id']}", {"done": False})
    assert reopened["pir"]["actions"][0]["done_at"] is None
    missing = await analyst.client.patch(
        f"{actions}/00000000-0000-0000-0000-000000000000", json={"done": True}
    )
    assert missing.status_code == 404
    assert (await audit_actions(owner, f"user:{analyst.id}")).count("pir.action_update") == 2
