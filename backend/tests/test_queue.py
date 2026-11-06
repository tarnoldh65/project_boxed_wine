from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx2 import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import MakeUser, SignedIn, audit_actions, new_tenant


@pytest.fixture
async def analyst(make_user: MakeUser, tenant: str) -> SignedIn:
    return await make_user({tenant: ["analyst"]})


async def new_case(
    client: AsyncClient, tenant: str, title: str = "x", template: str = "ddos", **changes: Any
) -> int:
    url = f"/api/tenants/{tenant}/cases"
    created = await client.post(url, json={"template": template, "title": title})
    number = created.json()["number"]
    if changes:
        changed = await client.patch(f"{url}/{number}", json=changes)
        assert changed.status_code == 200, changed.text
    return number


async def listed(client: AsyncClient, tenant: str, **params: Any) -> tuple[list[int], int]:
    response = await client.get(f"/api/tenants/{tenant}/cases", params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    return [c["number"] for c in body["cases"]], body["total"]


async def age(owner: AsyncEngine, tenant: str, number: int, **delta: float) -> None:
    async with owner.begin() as conn:
        await conn.execute(
            text(
                "UPDATE cases SET created_at = :at WHERE number = :number "
                "AND tenant_id = (SELECT id FROM tenants WHERE slug = :tenant)"
            ),
            {"at": datetime.now(UTC) - timedelta(**delta), "number": number, "tenant": tenant},
        )


async def test_lists_the_newest_cases_first_with_the_total(
    analyst: SignedIn, tenant: str, owner: AsyncEngine
) -> None:
    numbers = [await new_case(analyst.client, tenant) for _ in range(3)]

    assert await listed(analyst.client, tenant) == (numbers[::-1], 3)
    assert "case.list" in await audit_actions(owner, f"user:{analyst.id}")


async def test_pages_reach_every_match(analyst: SignedIn, tenant: str) -> None:
    numbers = [await new_case(analyst.client, tenant) for _ in range(5)]

    assert await listed(analyst.client, tenant, limit=2) == (numbers[:2:-1], 5)
    assert await listed(analyst.client, tenant, limit=2, offset=4) == (numbers[:1], 5)
    # The first case stays reachable however many came after it.
    first, total = await listed(analyst.client, tenant, limit=1, offset=4, sort="number")
    assert (first, total) == ([numbers[0]], 5)


async def test_filters_by_title_or_number_taking_the_text_literally(
    analyst: SignedIn, tenant: str
) -> None:
    client = analyst.client
    phish = await new_case(client, tenant, "Payroll PHISH")
    percent = await new_case(client, tenant, "100% encrypted")

    assert await listed(client, tenant, text="phish") == ([phish], 1)
    assert await listed(client, tenant, text=str(percent)) == ([percent], 1)
    assert await listed(client, tenant, text="%") == ([percent], 1)
    assert await listed(client, tenant, text="99999999999999") == ([], 0)


async def test_filters_by_state_severity_and_owner(analyst: SignedIn, tenant: str) -> None:
    client = analyst.client
    new = await new_case(client, tenant, impact="low", urgency="low")
    triage = await new_case(client, tenant, state="triage", impact="high", urgency="high")
    mine = await new_case(client, tenant, owner_id=str(analyst.id), impact="low", urgency="low")

    assert await listed(client, tenant, state="triage") == ([triage], 1)
    assert await listed(client, tenant, severity="critical") == ([triage], 1)
    assert await listed(client, tenant, owner=str(analyst.id)) == ([mine], 1)
    assert await listed(client, tenant, unassigned=True) == ([triage, new], 2)
    both = await listed(client, tenant, unassigned=True, severity="low")
    assert both == ([new], 1)


async def test_open_only_counts_as_the_dashboard_does(
    analyst: SignedIn, tenant: str, owner: AsyncEngine
) -> None:
    client = analyst.client
    open_case = await new_case(client, tenant)
    closed = await new_case(client, tenant)
    async with owner.begin() as conn:
        await conn.execute(
            text("UPDATE cases SET state = 'closed' WHERE number = :n"), {"n": closed}
        )
    merged = await new_case(client, tenant)
    assert (
        await client.post(f"/api/tenants/{tenant}/cases/{merged}/merge", json={"into": open_case})
    ).status_code == 200

    assert await listed(client, tenant, open_only=True) == ([open_case], 1)
    board = (await client.get(f"/api/tenants/{tenant}/dashboard")).json()
    assert sum(board["open_by_state"].values()) == 1


@pytest.mark.parametrize(
    ("bucket", "days"),
    [("under_1_day", 0.5), ("1_to_7_days", 3), ("7_to_30_days", 20), ("over_30_days", 90)],
)
async def test_filters_by_the_dashboard_age_buckets(
    analyst: SignedIn, tenant: str, owner: AsyncEngine, bucket: str, days: float
) -> None:
    expected = await new_case(analyst.client, tenant)
    other = await new_case(analyst.client, tenant)
    await age(owner, tenant, expected, days=days)
    await age(owner, tenant, other, days=days + 400)
    if bucket == "over_30_days":
        assert sorted((await listed(analyst.client, tenant, age=bucket))[0]) == [expected, other]
    else:
        assert await listed(analyst.client, tenant, age=bucket) == ([expected], 1)
    board = (await analyst.client.get(f"/api/tenants/{tenant}/dashboard")).json()
    assert board["aging"][bucket] >= 1


async def test_filters_and_sorts_by_sla_status_across_pages(
    analyst: SignedIn, tenant: str, owner: AsyncEngine
) -> None:
    client = analyst.client
    fresh = await new_case(client, tenant)
    late = await new_case(client, tenant)
    await age(owner, tenant, late, days=60)
    closed = await new_case(client, tenant)
    await client.post(f"/api/tenants/{tenant}/cases/{closed}/acknowledge")

    assert await listed(client, tenant, sla="breached") == ([late], 1)
    assert (await listed(client, tenant, sla="none"))[1] == 0
    worst_last, total = await listed(client, tenant, sort="sla")
    assert worst_last[-1] == late
    assert total == 3
    worst_first, _ = await listed(client, tenant, sort="sla", descending=False, limit=1)
    assert worst_first == [late]
    assert fresh in (await listed(client, tenant, sort="sla", descending=False, offset=1))[0]


async def test_sorts_by_each_column(
    analyst: SignedIn, tenant: str, make_user: MakeUser, owner: AsyncEngine
) -> None:
    client = analyst.client
    teammate = await make_user({tenant: ["analyst"]})
    async with owner.begin() as conn:
        await conn.execute(
            text("UPDATE users SET display_name = 'Aaron' WHERE id = :id"), {"id": teammate.id}
        )
    me = await client.get("/api/me")
    b = await new_case(
        client, tenant, "Bravo", state="triage", impact="high", urgency="high",
        owner_id=str(teammate.id),
    )  # fmt: skip
    a = await new_case(
        client, tenant, "Alpha", impact="low", urgency="low", owner_id=me.json()["id"]
    )
    c = await new_case(client, tenant, "Charlie", impact="medium", urgency="medium")

    async def order(sort: str, descending: bool = False) -> list[int]:
        return (await listed(client, tenant, sort=sort, descending=descending))[0]

    assert await order("number") == [b, a, c]
    assert await order("number", True) == [c, a, b]
    assert await order("title") == [a, b, c]
    assert await order("state") == [c, a, b]
    assert await order("severity") == [b, c, a]
    assert await order("severity", True) == [a, c, b]
    assert await order("created") == [b, a, c]
    # Unassigned cases sort last in both directions.
    assert (await order("owner"))[0] == b
    assert (await order("owner"))[-1] == c
    assert (await order("owner", True))[-1] == c


async def test_restricted_cases_stay_out_of_lists_and_counts(
    analyst: SignedIn, make_user: MakeUser, tenant: str, owner: AsyncEngine
) -> None:
    insider = await make_user({tenant: ["ir_lead", "restricted_case"]})
    await new_case(insider.client, tenant, "Hidden", restricted=True)
    shown = await new_case(analyst.client, tenant, "Shown")

    assert await listed(analyst.client, tenant) == ([shown], 1)
    assert await listed(analyst.client, tenant, text="Hidden") == ([], 0)
    assert (await listed(insider.client, tenant))[1] == 2
    assert await listed(analyst.client, tenant, sort="sla") == ([shown], 1)

    other = await make_user({await new_tenant(owner): ["analyst"]})
    assert (await other.client.get(f"/api/tenants/{tenant}/cases")).status_code == 403


async def test_rejects_bad_queue_parameters(analyst: SignedIn, tenant: str) -> None:
    url = f"/api/tenants/{tenant}/cases"
    for params in ({"limit": 501}, {"offset": -1}, {"sort": "nope"}, {"age": "ancient"}):
        assert (await analyst.client.get(url, params=params)).status_code == 422
