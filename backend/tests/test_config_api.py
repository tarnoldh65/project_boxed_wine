from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.tenant_config import DEFAULT_CONFIG
from tests.conftest import MakeUser, audit_actions, new_tenant

DEFAULT = DEFAULT_CONFIG.model_dump(mode="json")
STATES = [
    {"key": "open", "name": "Open"},
    {"key": "working", "name": "Working"},
    {"key": "done", "name": "Done", "terminal": True},
]


def url(tenant: str, section: str = "") -> str:
    return f"/api/tenants/{tenant}/config{section}"


async def test_tenant_starts_with_defaults(make_user: MakeUser, tenant: str) -> None:
    executive = await make_user({tenant: ["executive"]})
    response = await executive.client.get(url(tenant))
    assert response.status_code == 200
    assert response.json() == DEFAULT


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("/states", "states", STATES),
        (
            "/custom-fields",
            "custom_fields",
            [{"key": "vector", "label": "Vector", "type": "picklist", "options": ["email"]}],
        ),
        (
            "/severity",
            "severity",
            DEFAULT["severity"] | {"matrix": [["critical"] * 3, ["high"] * 3, ["low"] * 3]},
        ),
        (
            "/escalation-rules",
            "escalation_rules",
            [{"name": "Page", "trigger": "major_incident", "notify_emails": ["ir@example.com"]}],
        ),
    ],
)
async def test_admin_replaces_a_section(
    make_user: MakeUser, owner: AsyncEngine, tenant: str, section: str, key: str, value: Any
) -> None:
    admin = await make_user({tenant: ["admin"]})

    response = await admin.client.put(url(tenant, section), json=value)

    assert response.status_code == 200
    saved = (await admin.client.get(url(tenant))).json()
    assert saved[key] == response.json()[key]
    assert saved | {key: DEFAULT[key]} == DEFAULT
    assert await audit_actions(owner, f"user:{admin.id}") == ["config.update"]
    async with owner.connect() as conn:
        detail = await conn.scalar(
            text("SELECT detail FROM audit_log WHERE actor = :actor"),
            {"actor": f"user:{admin.id}"},
        )
    assert detail == {"data": saved[key]}


async def test_on_call_must_be_a_member(make_user: MakeUser, tenant: str) -> None:
    admin = await make_user({tenant: ["admin"]})
    stranger = await make_user({"insider-threat": ["analyst"]})
    teams = [{"key": "soc", "name": "SOC", "on_call": str(stranger.id)}]

    response = await admin.client.put(url(tenant, "/teams"), json=teams)

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", 0, "on_call"]
    teams[0]["on_call"] = str(admin.id)
    saved = await admin.client.put(url(tenant, "/teams"), json=teams)
    assert saved.json()["teams"] == teams


async def test_templates_can_reference_teams(make_user: MakeUser, tenant: str) -> None:
    admin = await make_user({tenant: ["admin"]})
    template = {"key": "phishing", "name": "Phish", "impact": "low", "urgency": "low",
                "team": "soc"}  # fmt: skip
    rejected = await admin.client.put(url(tenant, "/templates"), json=[template])
    assert "unknown team" in rejected.text
    await admin.client.put(url(tenant, "/teams"), json=[{"key": "soc", "name": "SOC"}])
    saved = await admin.client.put(url(tenant, "/templates"), json=[template])
    assert saved.json()["templates"] == [template | {"tlp": "amber", "pap": "amber", "tasks": []}]


async def test_changes_must_keep_references_valid(make_user: MakeUser, tenant: str) -> None:
    admin = await make_user({tenant: ["admin"]})
    field = {"key": "cause", "label": "Cause", "type": "text", "required_from": "recovery"}
    assert (await admin.client.put(url(tenant, "/custom-fields"), json=[field])).status_code == 200

    response = await admin.client.put(url(tenant, "/states"), json=STATES)

    assert response.status_code == 422
    assert "required from unknown state" in response.json()["detail"][0]["msg"]
    assert (await admin.client.get(url(tenant))).json()["states"] == DEFAULT["states"]


async def test_invalid_body_is_rejected(make_user: MakeUser, tenant: str) -> None:
    admin = await make_user({tenant: ["admin"]})
    response = await admin.client.put(url(tenant, "/states"), json=[{"key": "x"}])
    assert response.status_code == 422


async def test_configuration_is_per_tenant(make_user: MakeUser, owner: AsyncEngine) -> None:
    first, second = await new_tenant(owner), await new_tenant(owner)
    admin = await make_user({first: ["admin"], second: ["admin"]})

    await admin.client.put(url(first, "/states"), json=STATES)

    assert (await admin.client.get(url(second))).json()["states"] == DEFAULT["states"]


async def test_only_admins_change_configuration(
    make_user: MakeUser, owner: AsyncEngine, tenant: str
) -> None:
    analyst = await make_user({tenant: ["analyst", "restricted_case"]})

    assert (await analyst.client.get(url(tenant))).status_code == 200
    assert (await analyst.client.put(url(tenant, "/states"), json=STATES)).status_code == 403
    assert await audit_actions(owner, f"user:{analyst.id}") == ["access.denied"]
