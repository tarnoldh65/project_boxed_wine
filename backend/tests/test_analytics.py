from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from httpx2 import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app import sla
from app.analytics import CaseFacts, Metric, compute, sla_compliance
from tests.conftest import MakeUser, SignedIn, audit_actions, close_case

T0 = datetime(2026, 10, 1, tzinfo=UTC)


def at(hours: float) -> datetime:
    return T0 + timedelta(hours=hours)


# Fixed fixtures: times in hours after T0.
FACTS = [
    # Full incident with every milestone recorded.
    CaseFacts(
        id=uuid4(),
        created_at=at(10),
        category="ransomware",
        acknowledged_at=at(10.5),
        closed_at=at(100),
        resolution="true_positive",
        techniques=["T1486", "T1566.001"],
        milestones={
            "first_compromise": at(0),
            "detection": at(8),
            "containment": at(20),
            "eradication": at(50),
        },
    ),
    # Detection falls back to detected_at, containment to the SLA time, dwell to closing.
    CaseFacts(
        id=uuid4(),
        created_at=at(30),
        category="phishing",
        detected_at=at(28),
        acknowledged_at=at(31.5),
        contained_at=at(34),
        closed_at=at(40),
        resolution="true_positive",
        techniques=["T1566.001"],
        milestones={"first_compromise": at(4)},
    ),
    # A false positive: detection falls back to when the case was opened.
    CaseFacts(
        id=uuid4(),
        created_at=at(50),
        category="phishing",
        acknowledged_at=at(51),
        closed_at=at(52),
        resolution="false_positive",
    ),
    # Duplicates and merges are not incidents of their own.
    CaseFacts(
        id=uuid4(), created_at=at(60), category="phishing", closed_at=at(61), resolution="duplicate"
    ),
    # Still open and not acknowledged.
    CaseFacts(id=uuid4(), created_at=at(70), category="ddos"),
]


def test_metrics_on_fixed_fixtures() -> None:
    recurring = {FACTS[2].id}
    result = compute(FACTS, recurring, T0, at(100))

    assert result.cases == 5
    assert result.mttd == Metric(hours=(8 + 24) / 2, cases=2)
    assert result.mtta == Metric(hours=(0.5 + 1.5 + 1) / 3, cases=3)
    assert result.mttc == Metric(hours=(12 + 6) / 2, cases=2)
    # Closed incidents only: 92 h, 12 h, and 2 h for the false positive.
    assert result.mttr == Metric(hours=(92 + 12 + 2) / 3, cases=3)
    assert result.dwell_time == Metric(hours=(50 + 36) / 2, cases=2)
    assert result.closed == 3
    assert result.false_positive_rate == pytest.approx(1 / 3)
    assert result.by_category == {"phishing": 3, "ransomware": 1, "ddos": 1}
    assert result.by_technique == {"T1566.001": 2, "T1486": 1}
    assert (result.recurring, result.recurrence_rate) == (1, 0.2)


def test_metrics_without_cases() -> None:
    result = compute([], set(), T0, at(1))
    assert result.mttd == Metric(hours=None, cases=0)
    assert result.false_positive_rate is None
    assert result.recurrence_rate is None


def clock(status: sla.Status) -> sla.SlaClock:
    return sla.SlaClock(step="triage", due_at=T0, met_at=None, status=status)


def test_sla_compliance_counts_decided_steps() -> None:
    lists = [[clock("met"), clock("met"), clock("late")], [clock("breached"), clock("pending")]]
    assert sla_compliance(lists) == 0.5
    assert sla_compliance([[clock("pending")]]) is None


# --- API ----------------------------------------------------------------------------------


@pytest.fixture
async def analyst(make_user: MakeUser, tenant: str) -> SignedIn:
    return await make_user({tenant: ["analyst"]})


async def create(
    client: AsyncClient, tenant: str, template: str = "phishing", **change: Any
) -> int:
    response = await client.post(
        f"/api/tenants/{tenant}/cases", json={"template": template, "title": "x"}
    )
    assert response.status_code == 201, response.text
    number = response.json()["number"]
    if change:
        response = await client.patch(f"/api/tenants/{tenant}/cases/{number}", json=change)
        assert response.status_code == 200, response.text
    return number


async def backdate(owner: AsyncEngine, tenant: str, number: int, days: float) -> None:
    async with owner.begin() as conn:
        await conn.execute(
            text(
                "UPDATE cases SET created_at = now() - make_interval(secs => :s) "
                "FROM tenants t WHERE cases.tenant_id = t.id AND t.slug = :slug AND number = :n"
            ),
            {"s": days * 86400, "slug": tenant, "n": number},
        )


async def get(client: AsyncClient, path: str) -> Any:
    response = await client.get(path)
    assert response.status_code == 200, response.text
    return response.json()


async def test_dashboard(
    analyst: SignedIn, make_user: MakeUser, tenant: str, owner: AsyncEngine
) -> None:
    lead = await make_user({tenant: ["ir_lead", "restricted_case"]})
    await create(analyst.client, tenant, owner_id=str(analyst.id))
    old = await create(analyst.client, tenant, "ransomware", state="triage")
    await backdate(owner, tenant, old, 10)
    closed = await create(analyst.client, tenant)
    await close_case(analyst.client, tenant, closed, "benign")
    secret = await create(lead.client, tenant, "insider", restricted=True)
    actions = f"/api/tenants/{tenant}/cases/{secret}/pir/actions"
    assert (await lead.client.post(actions, json={"title": "Fix"})).status_code == 201

    board = await get(analyst.client, f"/api/tenants/{tenant}/dashboard")

    assert board["open_by_severity"] == {"high": 1, "critical": 1}
    assert board["open_by_state"] == {"new": 1, "triage": 1}
    assert board["aging"] == {"under_1_day": 1, "7_to_30_days": 1}
    assert {w["owner_id"]: w["open"] for w in board["workload"]} == {str(analyst.id): 1, None: 1}
    assert sum(board["sla_status"].values()) == 2
    assert board["sla_compliance"] == 1.0  # the closed case met every step on closing
    assert board["open_pir_actions"] == 0
    full = await get(lead.client, f"/api/tenants/{tenant}/dashboard")
    assert full["open_by_severity"] == {"high": 2, "critical": 1}
    assert full["open_pir_actions"] == 1
    assert "dashboard.view" in await audit_actions(owner, f"user:{analyst.id}")


async def test_metrics_api(analyst: SignedIn, tenant: str, owner: AsyncEngine) -> None:
    base = f"/api/tenants/{tenant}/cases"
    first = await create(analyst.client, tenant, attack_techniques=["T1566.001"])
    link = {"observables": [{"type": "domain", "value": "recur.example"}]}
    assert (await analyst.client.post(f"{base}/{first}/observables", json=link)).status_code == 201
    await close_case(analyst.client, tenant, first, "false_positive")
    again = await create(analyst.client, tenant)
    assert (await analyst.client.post(f"{base}/{again}/observables", json=link)).status_code == 201
    for when, milestone in (("2026-10-01T00:00:00Z", "first_compromise"),
                            ("2026-10-01T06:00:00Z", "detection")):  # fmt: skip
        event = {"occurred_at": when, "source": "EDR", "description": "x", "milestone": milestone}
        assert (
            await analyst.client.post(f"{base}/{again}/timeline", json=event)
        ).status_code == 201
    old = await create(analyst.client, tenant)
    await backdate(owner, tenant, old, 100)

    result = await get(analyst.client, f"/api/tenants/{tenant}/metrics")

    assert result["cases"] == 2  # the 100-day-old case is outside the default 90 days
    assert result["mttd"] == {"hours": 6.0, "cases": 1}
    assert result["false_positive_rate"] == 1.0
    assert result["by_technique"] == {"T1566.001": 1}
    assert (result["recurring"], result["recurrence_rate"]) == (1, 0.5)
    wide = await get(analyst.client, f"/api/tenants/{tenant}/metrics?days=365")
    assert wide["cases"] == 3
    assert "metrics.view" in await audit_actions(owner, f"user:{analyst.id}")
