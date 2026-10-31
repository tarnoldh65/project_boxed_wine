from typing import Any

import pytest
from httpx2 import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import MakeToken, MakeUser, SignedIn, audit_actions, new_tenant

PHISH = "Reset at hxxps://login.evil[.]example/reset from 198.51.100.23"


@pytest.fixture
async def analyst(make_user: MakeUser, tenant: str) -> SignedIn:
    return await make_user({tenant: ["analyst"]})


async def new_case(client: AsyncClient, tenant: str) -> int:
    response = await client.post(
        f"/api/tenants/{tenant}/cases", json={"template": "phishing", "title": "x"}
    )
    assert response.status_code == 201, response.text
    return response.json()["number"]


def case_url(tenant: str, number: int, suffix: str = "") -> str:
    return f"/api/tenants/{tenant}/cases/{number}{suffix}"


async def link(client: AsyncClient, tenant: str, number: int, *items: tuple[str, str]):
    return await client.post(
        case_url(tenant, number, "/observables"),
        json={"observables": [{"type": t, "value": v} for t, v in items]},
    )


def values(case: dict[str, Any]) -> list[tuple[str, str, int]]:
    return [(o["type"], o["value"], o["other_cases"]) for o in case["observables"]]


async def test_extract_from_text_proposes_candidates(analyst: SignedIn, tenant: str) -> None:
    number = await new_case(analyst.client, tenant)
    await link(analyst.client, tenant, number, ("ipv4", "198.51.100.23"))

    response = await analyst.client.post(
        case_url(tenant, number, "/observables/extract"), json={"text": PHISH}
    )

    assert response.status_code == 200, response.text
    assert response.json() == [
        {
            "type": "url",
            "value": "https://login.evil.example/reset",
            "defanged": "hxxps://login[.]evil[.]example/reset",
            "linked": False,
        },
        {
            "type": "ipv4",
            "value": "198.51.100.23",
            "defanged": "198[.]51[.]100[.]23",
            "linked": True,
        },
    ]


async def test_extract_from_alerts(
    analyst: SignedIn, tenant: str, make_token: MakeToken, client: AsyncClient
) -> None:
    token = await make_token(tenant, ["alerts:write"])
    created = await client.post(
        f"/api/tenants/{tenant}/alerts",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "template": "phishing",
            "title": "Mail from attacker[@]evil[.]example",
            "source": "mail-gateway",
            "payload": {"body": PHISH, "registry": [r"HKCU\Software\Evil\Run"]},
        },
    )
    number = created.json()["case_number"]

    response = await analyst.client.post(case_url(tenant, number, "/observables/extract"), json={})

    assert [(c["type"], c["value"]) for c in response.json()] == [
        ("email", "attacker@evil.example"),
        ("url", "https://login.evil.example/reset"),
        ("ipv4", "198.51.100.23"),
        ("registry_key", r"HKCU\Software\Evil\Run"),
    ]


async def test_link_normalizes_and_deduplicates(
    analyst: SignedIn, tenant: str, owner: AsyncEngine
) -> None:
    number = await new_case(analyst.client, tenant)

    response = await link(
        analyst.client,
        tenant,
        number,
        ("domain", "Evil[.]Example.com"),
        ("domain", "evil.example.com"),
        ("url", "hxxp://evil[.]example.com/x"),
        ("user", "CORP\\jdoe"),
    )

    assert response.status_code == 201, response.text
    assert values(response.json()) == [
        ("domain", "evil.example.com", 0),
        ("url", "http://evil.example.com/x", 0),
        ("user", "CORP\\jdoe", 0),
    ]
    defanged = {o["type"]: o["defanged"] for o in response.json()["observables"]}
    assert defanged["url"] == "hxxp://evil[.]example[.]com/x"
    again = await link(analyst.client, tenant, number, ("domain", "EVIL.example.com"))
    assert len(again.json()["observables"]) == 3
    assert "observable.link" in await audit_actions(owner, f"user:{analyst.id}")


@pytest.mark.parametrize(
    ("items", "message"),
    [
        ([{"type": "ipv4", "value": "999.1.1.1"}], "not a valid ipv4"),
        ([{"type": "md5", "value": "abc"}], "not a valid md5"),
        ([{"type": "mac", "value": "00:11"}], "Input should be"),
        ([], "at least 1 item"),
    ],
)
async def test_invalid_links(
    analyst: SignedIn, tenant: str, items: list[dict[str, str]], message: str
) -> None:
    number = await new_case(analyst.client, tenant)
    response = await analyst.client.post(
        case_url(tenant, number, "/observables"), json={"observables": items}
    )
    assert response.status_code == 422
    assert message in response.text


async def test_correlation_respects_restricted_cases(make_user: MakeUser, tenant: str) -> None:
    insider = await make_user({tenant: ["analyst", "restricted_case"]})
    analyst = await make_user({tenant: ["analyst"]})
    first = await new_case(analyst.client, tenant)
    second = await new_case(analyst.client, tenant)
    secret = await new_case(insider.client, tenant)
    await insider.client.patch(case_url(tenant, secret), json={"restricted": True})
    shared = ("ipv4", "203.0.113.7")
    for client, number in (
        (analyst.client, first),
        (analyst.client, second),
        (insider.client, secret),
    ):
        await link(client, tenant, number, shared)

    seen = (await analyst.client.get(case_url(tenant, first))).json()
    assert values(seen) == [("ipv4", "203.0.113.7", 1)]
    assert values((await insider.client.get(case_url(tenant, first))).json())[0][2] == 2

    page_url = f"/api/tenants/{tenant}/observables/{seen['observables'][0]['id']}"
    page = (await analyst.client.get(page_url)).json()
    assert page["defanged"] == "203[.]0[.]113[.]7"
    assert sorted(c["number"] for c in page["cases"]) == [first, second]
    insider_page = (await insider.client.get(page_url)).json()
    assert sorted(c["number"] for c in insider_page["cases"]) == [first, second, secret]


async def test_observables_are_per_tenant(
    make_user: MakeUser, tenant: str, owner: AsyncEngine
) -> None:
    other = await new_tenant(owner)
    user = await make_user({tenant: ["analyst"], other: ["analyst"]})
    here = await new_case(user.client, tenant)
    there = await new_case(user.client, other)
    item = ("domain", "shared.example")
    here_obs = (await link(user.client, tenant, here, item)).json()["observables"][0]
    there_obs = (await link(user.client, other, there, item)).json()["observables"][0]

    assert here_obs["id"] != there_obs["id"]
    assert (here_obs["other_cases"], there_obs["other_cases"]) == (0, 0)
    cross = await user.client.get(f"/api/tenants/{tenant}/observables/{there_obs['id']}")
    assert cross.status_code == 404


async def test_unlink(analyst: SignedIn, tenant: str, owner: AsyncEngine) -> None:
    number = await new_case(analyst.client, tenant)
    observable = (await link(analyst.client, tenant, number, ("cve", "CVE-2024-3094"))).json()
    url = case_url(tenant, number, f"/observables/{observable['observables'][0]['id']}")

    assert (await analyst.client.delete(url)).status_code == 204
    assert (await analyst.client.delete(url)).status_code == 404
    assert (await analyst.client.get(case_url(tenant, number))).json()["observables"] == []
    assert "observable.unlink" in await audit_actions(owner, f"user:{analyst.id}")


async def test_observable_page_not_found(analyst: SignedIn, tenant: str) -> None:
    response = await analyst.client.get(
        f"/api/tenants/{tenant}/observables/00000000-0000-0000-0000-000000000000"
    )
    assert response.status_code == 404


async def test_merge_and_split_carry_observables(analyst: SignedIn, tenant: str) -> None:
    source = await new_case(analyst.client, tenant)
    target = await new_case(analyst.client, tenant)
    linked = await link(
        analyst.client, tenant, source, ("ipv4", "10.0.0.1"), ("domain", "a.example")
    )
    await link(analyst.client, tenant, target, ("ipv4", "10.0.0.1"))
    domain_id = next(o["id"] for o in linked.json()["observables"] if o["type"] == "domain")

    split = await analyst.client.post(
        case_url(tenant, source, "/split"),
        json={"title": "Domain only", "observable_ids": [domain_id]},
    )
    assert split.status_code == 201, split.text
    assert values(split.json()) == [("domain", "a.example", 0)]

    merged = await analyst.client.post(case_url(tenant, source, "/merge"), json={"into": target})
    assert values(merged.json()) == [("ipv4", "10.0.0.1", 0)]
    assert (await analyst.client.get(case_url(tenant, source))).json()["observables"] == []

    foreign = await analyst.client.post(
        case_url(tenant, target, "/split"), json={"title": "x", "observable_ids": [domain_id]}
    )
    assert "observables must be linked to the case" in foreign.text


async def test_read_only_roles_cannot_link(make_user: MakeUser, tenant: str) -> None:
    analyst = await make_user({tenant: ["analyst"]})
    executive = await make_user({tenant: ["executive"]})
    number = await new_case(analyst.client, tenant)
    assert (await link(executive.client, tenant, number, ("ipv4", "10.0.0.1"))).status_code == 403
    extract = await executive.client.post(case_url(tenant, number, "/observables/extract"), json={})
    assert extract.status_code == 403
