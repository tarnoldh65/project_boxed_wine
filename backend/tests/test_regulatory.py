from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx2 import AsyncClient
from pydantic import ValidationError
from pytest import MonkeyPatch
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app import escalation, regulatory
from app.db import SessionLocal
from app.jobs import clocks as clocks_job
from app.models import RegulatoryClock
from app.tenant_config import DEFAULT_CONFIG, DEFAULT_REGIMES, Regime, TenantConfig
from tests.conftest import MakeUser, SignedIn, audit_actions
from tests.test_escalation import tenant_row

REGIMES = {r.key: r for r in DEFAULT_REGIMES}


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


# --- Deadline math ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("regime", "start", "due"),
    [
        ("gdpr", "2026-10-30T10:00", "2026-11-02T10:00"),  # 72 hours, weekends included
        ("hipaa", "2026-10-30T10:00", "2026-12-29T10:00"),  # 60 calendar days
        ("us_state", "2026-12-15T09:30", "2027-01-14T09:30"),  # 30 days across the year end
        ("nis2", "2026-10-31T22:00", "2026-11-01T22:00"),
        ("dora", "2026-10-31T22:00", "2026-11-01T02:00"),
        ("circia", "2026-11-25T08:00", "2026-11-28T08:00"),  # holidays do not matter
        ("sec_8k", "2026-11-04T15:00", "2026-11-10T15:00"),  # Wed: Thu, Fri, Mon, Tue
        ("sec_8k", "2026-10-31T15:00", "2026-11-05T15:00"),  # Sat: Mon to Thu
        ("sec_8k", "2026-11-06T15:00", "2026-11-13T15:00"),  # Veterans Day, Wed 11 Nov
        ("sec_8k", "2026-11-25T15:00", "2026-12-02T15:00"),  # Thanksgiving, Thu 26 Nov
        ("sec_8k", "2026-07-02T15:00", "2026-07-09T15:00"),  # July 4 observed on Fri 3 Jul
        ("sec_8k", "2027-12-22T15:00", "2027-12-29T15:00"),  # Christmas observed on Fri 24 Dec
        ("sec_8k", "2026-06-17T15:00", "2026-06-24T15:00"),  # Juneteenth, Fri 19 Jun
    ],
)
def test_deadlines(regime: str, start: str, due: str) -> None:
    assert regulatory.deadline(REGIMES[regime], utc(start)) == utc(due)


def clock(started: datetime, regime: Regime) -> RegulatoryClock:
    due = regulatory.deadline(regime, started)
    return RegulatoryClock(
        started_at=started, due_at=due, warn_at=due - timedelta(hours=regime.warn_hours_before)
    )


@pytest.mark.parametrize(
    ("hours", "status"),
    [(0, "pending"), (47.99, "pending"), (48, "warning"), (72, "warning"), (72.01, "breached")],
)
def test_gdpr_clock_warns_at_48_hours(hours: float, status: str) -> None:
    started = utc("2026-10-30T10:00")
    gdpr = clock(started, REGIMES["gdpr"])
    assert regulatory.status(gdpr, started + timedelta(hours=hours)) == status


@pytest.mark.parametrize(("met_hours", "status"), [(10, "met"), (72, "met"), (73, "late")])
def test_met_clock_status(met_hours: float, status: str) -> None:
    started = utc("2026-10-30T10:00")
    gdpr = clock(started, REGIMES["gdpr"])
    gdpr.met_at = started + timedelta(hours=met_hours)
    assert regulatory.status(gdpr, started + timedelta(days=30)) == status


@pytest.mark.parametrize(
    ("regime", "regulated", "regions", "applies"),
    [
        ("gdpr", ["pii"], ["EU"], True),
        ("gdpr", ["pci", "pii"], ["US", "GB"], True),
        ("gdpr", ["pii"], ["US-CA"], False),
        ("gdpr", ["phi"], ["EU"], False),
        ("gdpr", [], ["EU"], False),
        ("gdpr", ["pii"], [], False),
        ("hipaa", ["phi"], ["US-TX"], True),
        ("hipaa", ["phi"], ["USA"], False),  # a subdivision needs the hyphen
        ("sec_8k", [], [], True),  # materiality regimes ignore regulated data
        ("nis2", [], ["EU"], True),
        ("nis2", ["pii"], ["US"], False),
    ],
)
def test_regime_applies(
    regime: str, regulated: list[str], regions: list[str], applies: bool
) -> None:
    assert REGIMES[regime].applies(regulated, regions) is applies


def test_any_regulated_data_when_unspecified() -> None:
    regime = REGIMES["gdpr"].model_copy(update={"regulated_data": []})
    assert regime.applies(["cui"], ["EU"])


def test_regime_validation() -> None:
    with pytest.raises(ValidationError, match="starts on materiality"):
        Regime.model_validate(REGIMES["sec_8k"].model_dump() | {"regulated_data": ["pii"]})
    gdpr = REGIMES["gdpr"].model_dump()
    with pytest.raises(ValidationError, match="duplicate regime keys"):
        TenantConfig.model_validate(DEFAULT_CONFIG.model_dump() | {"regimes": [gdpr, gdpr]})


# --- API ----------------------------------------------------------------------------------


@pytest.fixture
async def analyst(make_user: MakeUser, tenant: str) -> SignedIn:
    return await make_user({tenant: ["analyst"]})


@pytest.fixture
async def counsel(make_user: MakeUser, tenant: str) -> SignedIn:
    return await make_user({tenant: ["legal"]})


def url(tenant: str, number: int, suffix: str = "") -> str:
    return f"/api/tenants/{tenant}/cases/{number}{suffix}"


async def new_case(client: AsyncClient, tenant: str, **flags: Any) -> dict[str, Any]:
    response = await client.post(
        f"/api/tenants/{tenant}/cases", json={"template": "data_exposure", "title": "x"}
    )
    assert response.status_code == 201, response.text
    case = response.json()
    if flags:
        response = await client.patch(url(tenant, case["number"]), json=flags)
        assert response.status_code == 200, response.text
        case = response.json()
    return case


async def post(client: AsyncClient, path: str, body: dict[str, Any]) -> Any:
    response = await client.post(path, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def by_regime(case: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["regime"]: c for c in case["clocks"]}


DECISION = {
    "material": True,
    "decided_by": "Disclosure committee",
    "decided_at": "2026-10-14T10:00:00-05:00",
    "rationale": "Customer data of a large share of accounts was taken.",
}
REPORT = {
    "recipient_type": "regulator",
    "recipient": "ICO",
    "sent_at": "2026-11-01T00:00:00Z",
    "summary": "Initial report, reference ICO-123",
}


async def test_flagging_regulated_data_starts_clocks(
    analyst: SignedIn, tenant: str, owner: AsyncEngine
) -> None:
    case = await new_case(analyst.client, tenant, regulated_data=["pii"], data_regions=["EU"])

    assert set(by_regime(case)) == {"gdpr"}
    gdpr = by_regime(case)["gdpr"]
    assert gdpr["name"] == "GDPR Art. 33 (supervisory authority)"
    assert gdpr["status"] == "pending"
    started = datetime.fromisoformat(gdpr["started_at"])
    assert datetime.fromisoformat(gdpr["due_at"]) - started == timedelta(hours=72)
    assert datetime.fromisoformat(gdpr["warn_at"]) - started == timedelta(hours=48)
    assert "clock.start" in await audit_actions(owner, f"user:{analyst.id}")

    # Adding a region starts the regimes that now apply; running clocks keep their start.
    response = await analyst.client.patch(
        url(tenant, case["number"]), json={"data_regions": ["EU", "US-CA"]}
    )
    clocks = by_regime(response.json())
    assert set(clocks) == {"gdpr", "us_state"}
    assert clocks["gdpr"]["started_at"] == gdpr["started_at"]


async def test_materiality_decision_starts_dependent_clocks(
    analyst: SignedIn, counsel: SignedIn, tenant: str, owner: AsyncEngine
) -> None:
    number = (await new_case(analyst.client, tenant, data_regions=["US"]))["number"]
    path = url(tenant, number, "/materiality")

    assert (await analyst.client.post(path, json=DECISION)).status_code == 403
    not_material = await post(counsel.client, path, DECISION | {"material": False})
    assert not_material["clocks"] == []

    case = await post(counsel.client, path, DECISION)

    assert [d["material"] for d in case["materiality"]] == [False, True]
    decision = case["materiality"][1]
    assert decision["decided_at"] == "2026-10-14T15:00:00Z"
    assert decision["decided_by"] == "Disclosure committee"
    assert decision["recorded_by"] == f"user:{counsel.id}"
    clocks = by_regime(case)
    assert set(clocks) == {"sec_8k", "circia"}
    assert clocks["sec_8k"]["started_at"] == "2026-10-14T15:00:00Z"
    assert clocks["sec_8k"]["due_at"] == "2026-10-20T15:00:00Z"
    assert clocks["sec_8k"]["status"] == "breached"  # the decision date is in the past
    assert await audit_actions(owner, f"user:{counsel.id}") == [
        "materiality.decide",
        "materiality.decide",
    ]


async def test_logging_a_notification_meets_its_clock(
    analyst: SignedIn, tenant: str, owner: AsyncEngine
) -> None:
    case = await new_case(analyst.client, tenant, regulated_data=["pii"], data_regions=["GB"])
    number = case["number"]
    evidence = await post(
        analyst.client,
        url(tenant, number, "/evidence/references"),
        {
            "name": "ico-report.pdf",
            "location": "DMS-4411",
            "collection": {"collected_by": "Legal", "collected_at": "2026-10-31T10:00:00Z"},
        },
    )
    report = REPORT | {
        "sent_at": datetime.now(UTC).isoformat(),
        "clock_id": by_regime(case)["gdpr"]["id"],
        "evidence_ids": [evidence["id"]],
    }

    case = await post(analyst.client, url(tenant, number, "/disclosures"), report)
    later = await post(
        analyst.client,
        url(tenant, number, "/disclosures"),
        report | {"sent_at": "2030-01-01T00:00:00Z", "evidence_ids": []},
    )

    assert by_regime(case)["gdpr"]["status"] == "met"
    # The first notification meets the clock; later ones do not move it.
    assert by_regime(later)["gdpr"]["met_at"] == by_regime(case)["gdpr"]["met_at"]
    logged = case["disclosures"][0]
    assert logged["recipient"] == "ICO"
    assert logged["evidence_ids"] == [evidence["id"]]
    assert logged["recorded_by"] == f"user:{analyst.id}"
    assert "disclosure.create" in await audit_actions(owner, f"user:{analyst.id}")

    other = await new_case(analyst.client, tenant)
    path = url(tenant, other["number"], "/disclosures")
    response = await analyst.client.post(path, json=report | {"evidence_ids": []})
    assert response.status_code == 422
    assert "the clock must belong to this case" in response.text
    response = await analyst.client.post(path, json=report | {"clock_id": None})
    assert response.status_code == 422
    assert "attachments must be evidence of this case" in response.text


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE disclosures SET summary = 'changed'",
        "DELETE FROM disclosures",
        "UPDATE materiality_decisions SET material = false",
        "DELETE FROM materiality_decisions",
    ],
)
async def test_decisions_and_notifications_are_append_only(statement: str) -> None:
    async with SessionLocal() as db:
        with pytest.raises(DBAPIError, match="permission denied"):
            await db.execute(text(statement))


async def test_active_clocks_across_cases(
    analyst: SignedIn, make_user: MakeUser, tenant: str, owner: AsyncEngine
) -> None:
    first = await new_case(analyst.client, tenant, regulated_data=["phi"], data_regions=["US"])
    second = await new_case(analyst.client, tenant, regulated_data=["pii"], data_regions=["EU"])
    lead = await make_user({tenant: ["ir_lead", "restricted_case"]})
    response = await lead.client.patch(url(tenant, first["number"]), json={"restricted": True})
    assert response.status_code == 200, response.text

    found = (await analyst.client.get(f"/api/tenants/{tenant}/clocks")).json()
    assert [(c["case_number"], c["regime"]) for c in found] == [(second["number"], "gdpr")]
    found = (await lead.client.get(f"/api/tenants/{tenant}/clocks")).json()
    assert [(c["case_number"], c["regime"]) for c in found] == [
        (second["number"], "gdpr"),
        (first["number"], "hipaa"),
    ]
    assert "clock.list" in await audit_actions(owner, f"user:{analyst.id}")


async def set_clock(owner: AsyncEngine, clock_id: str, **times: timedelta) -> None:
    """Move a clock's times relative to now."""
    assignments = ", ".join(f"{column} = now() + :{column}" for column in times)
    async with owner.begin() as conn:
        await conn.execute(
            text(f"UPDATE regulatory_clocks SET {assignments} WHERE id = :id"),  # noqa: S608
            {"id": clock_id, **times},
        )


async def test_clocks_job_warns_then_reports_the_breach(
    make_user: MakeUser,
    analyst: SignedIn,
    tenant: str,
    owner: AsyncEngine,
    monkeypatch: MonkeyPatch,
) -> None:
    admin = await make_user({tenant: ["admin"]})
    rules = [
        {"name": "Warn", "trigger": "clock_warning", "notify_emails": ["dpo@example.com"]},
        {"name": "Breach", "trigger": "clock_breach", "notify_emails": ["gc@example.com"]},
    ]
    response = await admin.client.put(f"/api/tenants/{tenant}/config/escalation-rules", json=rules)
    assert response.status_code == 200, response.text
    sent: list[tuple[list[str], dict[str, Any]]] = []
    monkeypatch.setattr(escalation, "send_email", lambda to, c: sent.append((to, c)))
    case = await new_case(analyst.client, tenant, regulated_data=["pii"], data_regions=["EU"])
    gdpr = by_regime(case)["gdpr"]
    tenant_ = await tenant_row(owner, tenant)

    await clocks_job.check_tenant(tenant_)  # pending: nothing to send
    assert await escalation.deliver(tenant_) == (0, 0)
    await set_clock(owner, gdpr["id"], warn_at=timedelta(minutes=-1))
    for _ in range(2):  # warns once
        await clocks_job.check_tenant(tenant_)
    await set_clock(owner, gdpr["id"], due_at=timedelta(minutes=-1))
    await clocks_job.check_tenant(tenant_)
    await escalation.deliver(tenant_)

    prefix = f"Case #{case['number']} (high): gdpr notification deadline"
    assert sorted((to, c["summary"]) for to, c in sent) == [
        (["dpo@example.com"], f"{prefix} approaching"),
        (["gc@example.com"], f"{prefix} missed"),
    ]
    assert await clocks_job.run() == 0


async def test_merge_moves_clocks_and_keeps_the_earlier_deadline(
    analyst: SignedIn, counsel: SignedIn, tenant: str, owner: AsyncEngine
) -> None:
    source = await new_case(analyst.client, tenant, regulated_data=["pii"], data_regions=["EU"])
    target = await new_case(analyst.client, tenant, regulated_data=["pii"], data_regions=["EU"])
    await set_clock(owner, by_regime(source)["gdpr"]["id"], started_at=timedelta(hours=-10),
                    due_at=timedelta(hours=62), warn_at=timedelta(hours=38))  # fmt: skip
    await post(counsel.client, url(tenant, source["number"], "/materiality"), DECISION)
    await post(analyst.client, url(tenant, source["number"], "/disclosures"), REPORT)

    response = await analyst.client.post(
        url(tenant, source["number"], "/merge"), json={"into": target["number"]}
    )
    assert response.status_code == 200, response.text

    merged = (await analyst.client.get(url(tenant, target["number"]))).json()
    clocks = by_regime(merged)
    assert set(clocks) == {"gdpr", "sec_8k", "nis2", "dora"}
    assert clocks["gdpr"]["id"] == by_regime(target)["gdpr"]["id"]
    due = datetime.fromisoformat(clocks["gdpr"]["due_at"])
    assert due - datetime.now(UTC) < timedelta(hours=62)
    assert [d["decided_by"] for d in merged["materiality"]] == ["Disclosure committee"]
    assert [d["recipient"] for d in merged["disclosures"]] == ["ICO"]
    found = (await analyst.client.get(f"/api/tenants/{tenant}/clocks")).json()
    assert {c["case_number"] for c in found} == {target["number"]}
