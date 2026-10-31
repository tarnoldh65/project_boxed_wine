from typing import Any

import pytest
from httpx2 import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth import hash_secret
from tests.conftest import MakeToken, MakeUser, audit_actions


@pytest.fixture
async def token(make_token: MakeToken, tenant: str) -> str:
    return await make_token(tenant, ["alerts:write"])


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def post_alert(client: AsyncClient, tenant: str, token: str, **body: Any):
    return await client.post(
        f"/api/tenants/{tenant}/alerts",
        json={"template": "ransomware", "title": "Encryption burst", "source": "edr"} | body,
        headers=bearer(token),
    )


async def get_case(client: AsyncClient, tenant: str, token: str, number: int) -> dict[str, Any]:
    response = await client.get(f"/api/tenants/{tenant}/cases/{number}", headers=bearer(token))
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
async def reader(make_token: MakeToken, tenant: str) -> str:
    return await make_token(tenant, ["cases:read"])


async def test_alert_opens_case_from_template(
    client: AsyncClient, tenant: str, token: str, reader: str, owner: AsyncEngine
) -> None:
    response = await post_alert(
        client,
        tenant,
        token,
        external_id="evt-1",
        description="Mass file renames",
        detected_at="2026-10-31T08:00:00Z",
        payload={"host": "fs-01", "count": 4000},
    )

    assert response.status_code == 201
    result = response.json()
    assert (result["new_case"], result["duplicate"]) == (True, False)
    case = await get_case(client, tenant, reader, result["case_number"])
    assert (case["category"], case["severity"], case["source"]) == ("ransomware", "critical", "edr")
    assert case["description"] == "Mass file renames"
    assert case["detected_at"] == "2026-10-31T08:00:00Z"
    assert case["alerts"][0]["payload"] == {"host": "fs-01", "count": 4000}
    actions = await audit_actions(owner, f"token:{await token_id(owner, token)}")
    assert actions == ["case.create", "alert.ingest"]


async def token_id(owner: AsyncEngine, raw: str) -> str:
    async with owner.connect() as conn:
        return str(
            await conn.scalar(
                text("SELECT id FROM api_tokens WHERE token_hash = :h"), {"h": hash_secret(raw)}
            )
        )


async def test_repeated_alert_is_deduplicated(client: AsyncClient, tenant: str, token: str) -> None:
    first = (await post_alert(client, tenant, token, external_id="evt-1")).json()
    again = await post_alert(client, tenant, token, external_id="evt-1", title="Retry")

    assert again.status_code == 200
    assert again.json() == first | {"new_case": False, "duplicate": True}
    # The same id from another source is a different alert.
    other = await post_alert(client, tenant, token, external_id="evt-1", source="siem")
    assert other.status_code == 201


async def test_grouped_alerts_share_an_open_case(
    client: AsyncClient, tenant: str, token: str, reader: str, make_user: MakeUser
) -> None:
    first = (await post_alert(client, tenant, token, group_key="incident-7")).json()
    second = (await post_alert(client, tenant, token, group_key="incident-7")).json()
    ungrouped = (await post_alert(client, tenant, token)).json()

    assert second["case_number"] == first["case_number"]
    assert second["new_case"] is False
    assert ungrouped["case_number"] != first["case_number"]
    case = await get_case(client, tenant, reader, first["case_number"])
    assert len(case["alerts"]) == 2

    analyst = await make_user({tenant: ["analyst"]})
    await analyst.client.patch(
        f"/api/tenants/{tenant}/cases/{first['case_number']}",
        json={"state": "closed", "resolution": "true_positive"},
    )
    after_close = (await post_alert(client, tenant, token, group_key="incident-7")).json()
    assert after_close["new_case"] is True


async def test_webhook_maps_named_fields(
    client: AsyncClient, tenant: str, token: str, reader: str
) -> None:
    payload = {"rule": "Impossible travel", "uid": 991, "correlation": "c-1", "user": "jdoe"}
    url = f"/api/tenants/{tenant}/webhooks/splunk"
    params = {
        "template": "bec",
        "title_field": "rule",
        "id_field": "uid",
        "group_field": "correlation",
    }

    response = await client.post(url, params=params, json=payload, headers=bearer(token))

    assert response.status_code == 201, response.text
    case = await get_case(client, tenant, reader, response.json()["case_number"])
    assert (case["title"], case["category"], case["source"]) == (
        "Impossible travel",
        "bec",
        "splunk",
    )
    alert = case["alerts"][0]
    assert (alert["external_id"], alert["group_key"], alert["payload"]) == ("991", "c-1", payload)
    retry = await client.post(url, params=params, json=payload, headers=bearer(token))
    assert retry.json()["duplicate"] is True


async def test_webhook_defaults(client: AsyncClient, tenant: str, token: str, reader: str) -> None:
    response = await client.post(
        f"/api/tenants/{tenant}/webhooks/guardduty",
        params={"template": "ddos"},
        json={"detail": {"severity": 8}},
        headers=bearer(token),
    )
    case = await get_case(client, tenant, reader, response.json()["case_number"])
    assert case["title"] == "guardduty alert"
    assert case["alerts"][0]["external_id"] is None


@pytest.mark.parametrize(
    ("url", "params", "body"),
    [
        ("/webhooks/splunk", {"template": "unknown"}, {}),
        ("/webhooks/Bad Source", {"template": "bec"}, {}),
        ("/webhooks/splunk", {}, {}),
        ("/webhooks/splunk", {"template": "bec"}, ["not", "an", "object"]),
        ("/alerts", {}, {"template": "bec", "title": "x", "source": "UPPER"}),
    ],
)
async def test_invalid_intake(
    client: AsyncClient, tenant: str, token: str, url: str, params: dict[str, str], body: Any
) -> None:
    response = await client.post(
        f"/api/tenants/{tenant}{url}", params=params, json=body, headers=bearer(token)
    )
    assert response.status_code in (404, 422)


async def test_intake_needs_the_scope(
    client: AsyncClient, tenant: str, reader: str, make_user: MakeUser
) -> None:
    assert (await post_alert(client, tenant, reader)).status_code == 403
    analyst = await make_user({tenant: ["analyst"]})
    response = await analyst.client.post(
        f"/api/tenants/{tenant}/alerts",
        json={"template": "bec", "title": "x", "source": "manual"},
    )
    assert response.status_code == 403
