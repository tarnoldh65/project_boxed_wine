from typing import Any

import pytest
from httpx2 import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import (
    MakeToken,
    MakeUser,
    SignedIn,
    audit_actions,
    close_case,
    new_tenant,
)


def cases_url(tenant: str, suffix: str = "") -> str:
    return f"/api/tenants/{tenant}/cases{suffix}"


async def create(client: AsyncClient, tenant: str, **body: Any) -> dict[str, Any]:
    response = await client.post(
        cases_url(tenant), json={"template": "phishing", "title": "Suspicious email"} | body
    )
    assert response.status_code == 201, response.text
    return response.json()


async def patch(client: AsyncClient, tenant: str, number: int, **body: Any):
    return await client.patch(cases_url(tenant, f"/{number}"), json=body)


@pytest.fixture
async def analyst(make_user: MakeUser, tenant: str) -> SignedIn:
    return await make_user({tenant: ["analyst"]})


def errors(response: Any) -> list[str]:
    return [d["msg"].removeprefix("Value error, ") for d in response.json()["detail"]]


async def test_manual_case_starts_from_template(analyst: SignedIn, tenant: str) -> None:
    first = await create(analyst.client, tenant)
    second = await create(
        analyst.client, tenant, template="insider", title="Data hoarding", impact="low"
    )

    assert (first["number"], second["number"]) == (1, 2)
    assert (
        first
        | {
            "state": "new",
            "category": "phishing",
            "impact": "medium",
            "urgency": "high",
            "severity": "high",
            "priority": "P2",
            "tlp": "amber",
            "source": "manual",
            "owner_id": None,
            "restricted": False,
            "alerts": [],
            "assets": [],
        }
        == first
    )
    assert (second["severity"], second["tlp"], second["pap"]) == ("low", "red", "red")


async def test_numbers_are_per_tenant(make_user: MakeUser, tenant: str, owner: AsyncEngine) -> None:
    other = await new_tenant(owner)
    user = await make_user({tenant: ["analyst"], other: ["analyst"]})
    await create(user.client, tenant)
    assert (await create(user.client, other))["number"] == 1


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"template": "nope"}, "unknown template"),
        ({"impact": "extreme"}, "unknown impact"),
        ({"urgency": "someday"}, "unknown urgency"),
        ({"title": "  "}, "at least 1 character"),
    ],
)
async def test_invalid_create(analyst: SignedIn, tenant: str, body: dict[str, Any], message: str):
    response = await analyst.client.post(
        cases_url(tenant), json={"template": "phishing", "title": "x"} | body
    )
    assert response.status_code == 422
    assert message in response.text


async def test_read_only_roles_cannot_create(make_user: MakeUser, tenant: str) -> None:
    executive = await make_user({tenant: ["executive"]})
    response = await executive.client.post(
        cases_url(tenant), json={"template": "phishing", "title": "x"}
    )
    assert response.status_code == 403


async def test_view_and_list_are_audited(
    analyst: SignedIn, tenant: str, owner: AsyncEngine
) -> None:
    case = await create(analyst.client, tenant)
    assert (await analyst.client.get(cases_url(tenant, f"/{case['number']}"))).status_code == 200
    listed = (await analyst.client.get(cases_url(tenant))).json()
    assert [c["number"] for c in listed] == [case["number"]]
    actions = await audit_actions(owner, f"user:{analyst.id}")
    assert [a for a in actions if a.startswith("case.")] == [
        "case.create",
        "case.view",
        "case.list",
    ]


async def test_list_filters_by_state(analyst: SignedIn, tenant: str) -> None:
    await create(analyst.client, tenant)
    second = await create(analyst.client, tenant)
    await patch(analyst.client, tenant, second["number"], state="triage")
    listed = (await analyst.client.get(cases_url(tenant), params={"state": "triage"})).json()
    assert [c["number"] for c in listed] == [second["number"]]


async def test_unknown_case_is_not_found(analyst: SignedIn, tenant: str) -> None:
    assert (await analyst.client.get(cases_url(tenant, "/999"))).status_code == 404


async def test_restricted_cases_are_hidden(make_user: MakeUser, tenant: str) -> None:
    insider = await make_user({tenant: ["analyst", "restricted_case"]})
    analyst = await make_user({tenant: ["analyst"]})
    case = await create(insider.client, tenant, title="Executive mailbox access")
    assert (await patch(insider.client, tenant, case["number"], restricted=True)).status_code == 200

    assert (await analyst.client.get(cases_url(tenant))).json() == []
    assert (await analyst.client.get(cases_url(tenant, f"/{case['number']}"))).status_code == 404
    assert (await patch(analyst.client, tenant, case["number"], title="x")).status_code == 404
    assert len((await insider.client.get(cases_url(tenant))).json()) == 1


async def test_restricting_needs_permission(
    analyst: SignedIn, tenant: str, owner: AsyncEngine
) -> None:
    case = await create(analyst.client, tenant)
    response = await patch(analyst.client, tenant, case["number"], restricted=True)
    assert response.status_code == 403
    assert (await audit_actions(owner, f"user:{analyst.id}"))[-1] == "access.denied"


async def test_update_changes_fields_and_is_audited(
    analyst: SignedIn, tenant: str, owner: AsyncEngine
) -> None:
    case = await create(analyst.client, tenant)
    response = await patch(
        analyst.client,
        tenant,
        case["number"],
        title="Credential phish",
        tlp="green",
        regulated_data=["pii"],
        data_regions=["EU"],
        business_units=["Finance"],
        attack_techniques=["T1566.001"],
        detected_at="2026-10-30T12:00:00Z",
        owner_id=str(analyst.id),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "Credential phish"
    assert body["regulated_data"] == ["pii"]
    assert body["detected_at"] == "2026-10-30T12:00:00Z"
    async with owner.connect() as conn:
        detail = await conn.scalar(
            text("SELECT detail FROM audit_log WHERE actor = :actor AND action = 'case.update'"),
            {"actor": f"user:{analyst.id}"},
        )
    assert detail["changes"]["attack_techniques"] == ["T1566.001"]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"title": None}, "cannot be null: title"),
        ({"state": "limbo"}, "unknown state"),
        ({"team": "ghosts"}, "unknown team"),
        ({"data_regions": ["eu"]}, "pattern"),
        ({"attack_techniques": ["T15"]}, "pattern"),
        ({"regulated_data": ["gdpr"]}, "Input should be"),
        ({"owner_id": "00000000-0000-0000-0000-000000000000"}, "owner must be a tenant member"),
        ({"resolution": "benign"}, "only closed cases have a resolution"),
        ({"resolution": "merged"}, "marked merged by merging"),
        ({"state": "closed"}, "closing a case needs a resolution"),
        ({"override": {"severity": "critical", "justification": " "}}, "at least 1 character"),
        ({"override": {"severity": "catastrophic", "justification": "why"}}, "unknown severity"),
    ],
)
async def test_invalid_updates(
    analyst: SignedIn, tenant: str, body: dict[str, Any], message: str
) -> None:
    case = await create(analyst.client, tenant)
    response = await patch(analyst.client, tenant, case["number"], **body)
    assert response.status_code == 422
    assert message in response.text


async def test_severity_follows_matrix_until_overridden(analyst: SignedIn, tenant: str) -> None:
    number = (await create(analyst.client, tenant))["number"]

    rated = (await patch(analyst.client, tenant, number, impact="high")).json()
    assert (rated["severity"], rated["priority"]) == ("critical", "P1")

    override = {"severity": "low", "justification": "Test mailbox only"}
    overridden = (await patch(analyst.client, tenant, number, override=override)).json()
    assert (overridden["severity"], overridden["priority"]) == ("low", "P4")
    assert overridden["override_justification"] == "Test mailbox only"

    # Changing impact keeps an override in force; clearing it re-rates from the matrix.
    kept = (await patch(analyst.client, tenant, number, impact="medium")).json()
    assert kept["severity"] == "low"
    cleared = (await patch(analyst.client, tenant, number, override=None)).json()
    assert (cleared["severity"], cleared["override_justification"]) == ("high", None)


async def test_close_and_reopen(analyst: SignedIn, tenant: str) -> None:
    number = (await create(analyst.client, tenant))["number"]

    closed = await close_case(analyst.client, tenant, number, "false_positive")
    assert closed["resolution"] == "false_positive"
    assert closed["closed_at"] is not None

    reopened = (await patch(analyst.client, tenant, number, state="analysis")).json()
    assert (reopened["resolution"], reopened["closed_at"]) == (None, None)


async def configure(make_user: MakeUser, tenant: str, section: str, value: Any) -> None:
    admin = await make_user({tenant: ["admin"]})
    response = await admin.client.put(f"/api/tenants/{tenant}/config/{section}", json=value)
    assert response.status_code == 200, response.text


async def test_custom_fields_are_validated_for_the_state(
    make_user: MakeUser, analyst: SignedIn, tenant: str
) -> None:
    fields = [
        {"key": "root_cause", "label": "Root cause", "type": "text", "required_from": "recovery"},
        {"key": "handler", "label": "Handler", "type": "user"},
    ]
    await configure(make_user, tenant, "custom-fields", fields)
    number = (await create(analyst.client, tenant))["number"]

    assert (await patch(analyst.client, tenant, number, state="eradication")).status_code == 200
    blocked = await patch(analyst.client, tenant, number, state="recovery")
    assert blocked.status_code == 422
    assert blocked.json()["detail"][0]["loc"] == ["body", "custom", "root_cause"]

    stranger = await patch(analyst.client, tenant, number, custom={"handler": "nobody"})
    assert errors(stranger) == ["not a tenant member"]

    ok = await patch(
        analyst.client,
        tenant,
        number,
        state="recovery",
        custom={"root_cause": "Reused password", "handler": "tester"},
    )
    assert ok.status_code == 200, ok.text


async def test_on_call_owns_new_cases(make_user: MakeUser, analyst: SignedIn, tenant: str) -> None:
    team = {"key": "soc", "name": "SOC", "on_call": str(analyst.id)}
    await configure(make_user, tenant, "teams", [team])
    templates = [{"key": "phishing", "name": "Phishing", "impact": "low", "urgency": "low",
                  "team": "soc"}]  # fmt: skip
    await configure(make_user, tenant, "templates", templates)

    case = await create(analyst.client, tenant)

    assert (case["team"], case["owner_id"], case["severity"]) == ("soc", str(analyst.id), "low")


async def test_assets_are_linked_and_unlinked(
    analyst: SignedIn, tenant: str, owner: AsyncEngine
) -> None:
    number = (await create(analyst.client, tenant))["number"]
    url = cases_url(tenant, f"/{number}/assets")

    host = (await analyst.client.post(url, json={"kind": "host", "name": "web-01"})).json()
    again = await analyst.client.post(
        url, json={"kind": "host", "name": "web-01", "external_id": "CI-42"}
    )
    assert again.json() == host | {"external_id": "CI-42"}
    # A later link without an external id keeps the one already known.
    kept = await analyst.client.post(url, json={"kind": "host", "name": "web-01"})
    assert kept.json()["external_id"] == "CI-42"
    await analyst.client.post(url, json={"kind": "account", "name": "svc_backup"})

    case = (await analyst.client.get(cases_url(tenant, f"/{number}"))).json()
    assert [(a["kind"], a["name"]) for a in case["assets"]] == [
        ("account", "svc_backup"),
        ("host", "web-01"),
    ]

    assert (await analyst.client.delete(f"{url}/{host['id']}")).status_code == 204
    assert (await analyst.client.delete(f"{url}/{host['id']}")).status_code == 404
    assert "case.asset_unlink" in await audit_actions(owner, f"user:{analyst.id}")

    search = f"/api/tenants/{tenant}/assets"
    found = (await analyst.client.get(search, params={"q": "WEB"})).json()
    assert [a["name"] for a in found] == ["web-01"]
    assert (await analyst.client.get(search, params={"q": "%"})).json() == []


async def test_members_are_listed(analyst: SignedIn, tenant: str) -> None:
    members = (await analyst.client.get(f"/api/tenants/{tenant}/members")).json()
    assert members == [{"id": str(analyst.id), "username": "tester", "display_name": "Test User"}]


async def ingest(client: AsyncClient, tenant: str, token: str, **body: Any) -> dict[str, Any]:
    response = await client.post(
        f"/api/tenants/{tenant}/alerts",
        json={"template": "phishing", "title": "Alert", "source": "siem"} | body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


async def test_merge_moves_everything(
    analyst: SignedIn,
    tenant: str,
    make_token: MakeToken,
    client: AsyncClient,
    owner: AsyncEngine,
) -> None:
    token = await make_token(tenant, ["alerts:write"])
    source = (await ingest(client, tenant, token, external_id="a1"))["case_number"]
    target = (await ingest(client, tenant, token, external_id="a2"))["case_number"]
    assets = cases_url(tenant, f"/{source}/assets")
    await analyst.client.post(assets, json={"kind": "host", "name": "web-01"})
    await analyst.client.post(
        cases_url(tenant, f"/{target}/assets"), json={"kind": "host", "name": "web-01"}
    )
    await analyst.client.post(assets, json={"kind": "account", "name": "jdoe"})

    response = await analyst.client.post(
        cases_url(tenant, f"/{source}/merge"), json={"into": target}
    )

    assert response.status_code == 200, response.text
    merged = response.json()
    assert sorted(a["external_id"] for a in merged["alerts"]) == ["a1", "a2"]
    assert [a["name"] for a in merged["assets"]] == ["jdoe", "web-01"]
    closed = (await analyst.client.get(cases_url(tenant, f"/{source}"))).json()
    assert (closed["state"], closed["resolution"], closed["merged_into"]) == (
        "closed",
        "merged",
        target,
    )
    assert (closed["alerts"], closed["assets"]) == ([], [])
    actions = await audit_actions(owner, f"user:{analyst.id}")
    assert {"case.merge", "case.absorb"} <= set(actions)


async def test_merge_rules(make_user: MakeUser, tenant: str) -> None:
    insider = await make_user({tenant: ["analyst", "restricted_case"]})
    open_case = (await create(insider.client, tenant))["number"]
    restricted = (await create(insider.client, tenant))["number"]
    await patch(insider.client, tenant, restricted, restricted=True)
    closed = (await create(insider.client, tenant))["number"]
    await close_case(insider.client, tenant, closed)

    async def merge(source: int, into: int) -> list[str]:
        response = await insider.client.post(
            cases_url(tenant, f"/{source}/merge"), json={"into": into}
        )
        return errors(response)

    assert await merge(open_case, open_case) == ["a case cannot be merged into itself"]
    assert await merge(open_case, closed) == ["only open cases can be merged"]
    assert await merge(restricted, open_case) == [
        "a restricted case can only merge into a restricted case"
    ]
    response = await insider.client.post(
        cases_url(tenant, f"/{open_case}/merge"), json={"into": 999}
    )
    assert response.status_code == 404


async def test_split_moves_the_selection(
    analyst: SignedIn,
    tenant: str,
    make_token: MakeToken,
    client: AsyncClient,
) -> None:
    token = await make_token(tenant, ["alerts:write"])
    first = await ingest(client, tenant, token, group_key="g", external_id="x1")
    second = await ingest(client, tenant, token, group_key="g", external_id="x2")
    number = first["case_number"]
    assets = cases_url(tenant, f"/{number}/assets")
    host = (await analyst.client.post(assets, json={"kind": "host", "name": "h1"})).json()
    await analyst.client.post(assets, json={"kind": "host", "name": "h2"})
    await patch(analyst.client, tenant, number, impact="high", tlp="red")

    response = await analyst.client.post(
        cases_url(tenant, f"/{number}/split"),
        json={"title": "Second wave", "alert_ids": [second["alert_id"]], "asset_ids": [host["id"]]},
    )

    assert response.status_code == 201, response.text
    new = response.json()
    assert (new["title"], new["state"], new["source"]) == ("Second wave", "new", "split")
    assert (new["severity"], new["tlp"]) == ("critical", "red")
    assert [a["id"] for a in new["alerts"]] == [second["alert_id"]]
    assert [a["name"] for a in new["assets"]] == ["h1"]
    remaining = (await analyst.client.get(cases_url(tenant, f"/{number}"))).json()
    assert [a["id"] for a in remaining["alerts"]] == [first["alert_id"]]
    assert [a["name"] for a in remaining["assets"]] == ["h2"]


async def test_split_rules(analyst: SignedIn, tenant: str) -> None:
    number = (await create(analyst.client, tenant))["number"]
    other = (await create(analyst.client, tenant))["number"]
    host = await analyst.client.post(
        cases_url(tenant, f"/{other}/assets"), json={"kind": "host", "name": "h"}
    )
    url = cases_url(tenant, f"/{number}/split")

    nothing = await analyst.client.post(url, json={"title": "x"})
    assert errors(nothing) == ["select at least one alert, asset, or observable to split off"]
    foreign = await analyst.client.post(url, json={"title": "x", "asset_ids": [host.json()["id"]]})
    assert errors(foreign) == ["assets must be linked to the case"]
    stray = await analyst.client.post(
        url, json={"title": "x", "alert_ids": ["00000000-0000-0000-0000-000000000000"]}
    )
    assert errors(stray) == ["alerts must belong to the case"]
    await close_case(analyst.client, tenant, number)
    closed = await analyst.client.post(
        url, json={"title": "x", "alert_ids": ["00000000-0000-0000-0000-000000000000"]}
    )
    assert errors(closed) == ["only open cases can be split"]
