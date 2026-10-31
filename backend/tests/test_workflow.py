from typing import Any

import pytest
from httpx2 import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import MakeUser, SignedIn, audit_actions, close_case


@pytest.fixture
async def analyst(make_user: MakeUser, tenant: str) -> SignedIn:
    return await make_user({tenant: ["analyst"]})


def url(tenant: str, suffix: str = "") -> str:
    return f"/api/tenants/{tenant}/cases{suffix}"


async def create(client: AsyncClient, tenant: str, **body: Any) -> dict[str, Any]:
    response = await client.post(url(tenant), json={"template": "phishing", "title": "x"} | body)
    assert response.status_code == 201, response.text
    return response.json()


async def patch(client: AsyncClient, tenant: str, number: int, **body: Any):
    return await client.patch(url(tenant, f"/{number}"), json=body)


async def patch_task(client: AsyncClient, tenant: str, number: int, task: str, **body: Any):
    return await client.patch(url(tenant, f"/{number}/tasks/{task}"), json=body)


def by_key(case: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {t["playbook_key"]: t for t in case["tasks"]}


async def test_playbook_tasks_are_created_with_dependencies(analyst: SignedIn, tenant: str) -> None:
    case = await create(analyst.client, tenant)

    tasks = by_key(case)
    assert list(tasks) == ["recipients", "block", "reset", "purge"]
    assert tasks["reset"]["depends_on"] == [tasks["recipients"]["id"]]
    assert tasks["reset"]["blocked"] is True
    assert tasks["block"]["blocked"] is False
    assert tasks["recipients"]["due_at"] is not None
    assert tasks["purge"]["due_at"] is None


async def test_conditional_task_appears_once_the_case_matches(
    analyst: SignedIn, tenant: str
) -> None:
    case = await create(analyst.client, tenant, template="ransomware", impact="medium")
    assert "retainer" not in by_key(case)

    raised = (await patch(analyst.client, tenant, case["number"], impact="high")).json()
    assert raised["severity"] == "critical"
    assert "retainer" in by_key(raised)
    lowered = (await patch(analyst.client, tenant, case["number"], impact="medium")).json()
    assert len(lowered["tasks"]) == len(raised["tasks"])  # created tasks are kept


async def test_closing_requires_finished_tasks(analyst: SignedIn, tenant: str) -> None:
    case = await create(analyst.client, tenant, template="ddos")

    response = await patch(
        analyst.client, tenant, case["number"], state="closed", resolution="benign"
    )

    assert response.status_code == 422
    assert "finish or waive every task first: Engage the DDoS" in response.text
    closed = await close_case(analyst.client, tenant, case["number"])
    assert [t["status"] for t in closed["tasks"]] == ["waived", "waived"]


async def test_task_lifecycle(analyst: SignedIn, tenant: str, owner: AsyncEngine) -> None:
    case = await create(analyst.client, tenant, template="ddos")
    number = case["number"]
    provider, monitor = (by_key(case)[k]["id"] for k in ("provider", "monitor"))

    blocked = await patch_task(analyst.client, tenant, number, monitor, status="in_progress")
    assert blocked.status_code == 422
    assert "blocked by unfinished dependencies" in blocked.text

    done = await patch_task(
        analyst.client,
        tenant,
        number,
        provider,
        status="done",
        assignee_id=str(analyst.id),
        checklist=[{"text": "Ticket opened", "done": True}],
    )
    assert done.status_code == 200, done.text
    task = by_key(done.json())["provider"]
    assert (task["status"], task["assignee_id"]) == ("done", str(analyst.id))
    assert task["checklist"] == [{"text": "Ticket opened", "done": True}]
    assert by_key(done.json())["monitor"]["blocked"] is False

    cycle = await patch_task(analyst.client, tenant, number, provider, depends_on=[monitor])
    assert "would form a cycle" in cycle.text
    assert "task.update" in await audit_actions(owner, f"user:{analyst.id}")


async def test_manual_tasks(analyst: SignedIn, tenant: str, make_user: MakeUser) -> None:
    case = await create(analyst.client, tenant, template="ddos")
    number = case["number"]
    provider = by_key(case)["provider"]["id"]
    tasks_url = url(tenant, f"/{number}/tasks")

    added = await analyst.client.post(
        tasks_url,
        json={
            "title": "Brief the CISO",
            "depends_on": [provider],
            "due_at": "2026-11-02T09:00:00Z",
            "checklist": [{"text": "Draft"}, {"text": "Send"}],
        },
    )

    assert added.status_code == 201, added.text
    manual = added.json()["tasks"][-1]
    assert (manual["title"], manual["playbook_key"], manual["blocked"]) == (
        "Brief the CISO",
        None,
        True,
    )
    assert manual["checklist"] == [
        {"text": "Draft", "done": False},
        {"text": "Send", "done": False},
    ]

    stranger = await make_user({"insider-threat": ["analyst"]})
    bad = [
        ({"title": "x", "assignee_id": str(stranger.id)}, "assignee must be a tenant member"),
        ({"title": "x", "depends_on": ["00000000-0000-0000-0000-000000000000"]}, "other tasks"),
        ({"title": " "}, "at least 1 character"),
    ]
    for body, message in bad:
        response = await analyst.client.post(tasks_url, json=body)
        assert response.status_code == 422
        assert message in response.text
    response = await patch_task(
        analyst.client, tenant, number, manual["id"], assignee_id=str(stranger.id)
    )
    assert "assignee must be a tenant member" in response.text
    missing = await patch_task(
        analyst.client, tenant, number, "00000000-0000-0000-0000-000000000000", title="x"
    )
    assert missing.status_code == 404


async def test_tasks_of_closed_cases_are_read_only(analyst: SignedIn, tenant: str) -> None:
    case = await close_case(
        analyst.client, tenant, (await create(analyst.client, tenant))["number"]
    )
    task = case["tasks"][0]["id"]
    response = await patch_task(analyst.client, tenant, case["number"], task, status="open")
    assert "tasks of a closed case cannot change" in response.text
    added = await analyst.client.post(url(tenant, f"/{case['number']}/tasks"), json={"title": "x"})
    assert added.status_code == 422


async def test_states_meet_sla_milestones(analyst: SignedIn, tenant: str) -> None:
    number = (await create(analyst.client, tenant))["number"]

    triage = (await patch(analyst.client, tenant, number, state="triage")).json()
    assert triage["acknowledged_at"] is not None
    assert triage["triaged_at"] is None

    analysis = (await patch(analyst.client, tenant, number, state="analysis")).json()
    assert analysis["triaged_at"] is not None
    assert analysis["contained_at"] is None
    statuses = {c["step"]: c["status"] for c in analysis["sla"]}
    assert statuses == {
        "acknowledge": "met",
        "triage": "met",
        "contain": "pending",
        "resolve": "pending",
    }

    eradication = (await patch(analyst.client, tenant, number, state="eradication")).json()
    assert eradication["contained_at"] is not None


async def test_closing_meets_every_milestone(analyst: SignedIn, tenant: str) -> None:
    closed = await close_case(
        analyst.client, tenant, (await create(analyst.client, tenant))["number"]
    )
    assert [c["status"] for c in closed["sla"]] == ["met"] * 4
    assert closed["sla_status"] == "met"


async def test_acknowledge(analyst: SignedIn, tenant: str, owner: AsyncEngine) -> None:
    number = (await create(analyst.client, tenant))["number"]
    ack_url = url(tenant, f"/{number}/acknowledge")

    acknowledged = (await analyst.client.post(ack_url)).json()

    assert acknowledged["acknowledged_at"] is not None
    assert acknowledged["owner_id"] == str(analyst.id)
    assert "already acknowledged" in (await analyst.client.post(ack_url)).text
    assert "case.acknowledge" in await audit_actions(owner, f"user:{analyst.id}")


async def test_list_shows_the_worst_sla_status(
    analyst: SignedIn, tenant: str, owner: AsyncEngine
) -> None:
    number = (await create(analyst.client, tenant))["number"]
    async with owner.begin() as conn:
        await conn.execute(
            text(
                "UPDATE cases SET created_at = now() - interval '25 minutes' "
                "FROM tenants t WHERE cases.tenant_id = t.id AND t.slug = :slug AND number = :n"
            ),
            {"slug": tenant, "n": number},
        )
    listed = (await analyst.client.get(url(tenant))).json()
    # Phishing is rated high: acknowledge within 30 minutes, so 25 minutes in is a warning.
    assert listed[0]["sla_status"] == "warning"


async def test_major_incident(analyst: SignedIn, tenant: str, owner: AsyncEngine) -> None:
    number = (await create(analyst.client, tenant))["number"]
    major_url = url(tenant, f"/{number}/major-incident")

    declared = await analyst.client.post(major_url, json={"reason": "Payroll system down"})

    assert declared.status_code == 200, declared.text
    body = declared.json()
    assert (body["major_incident_reason"], body["major_incident_at"] is not None) == (
        "Payroll system down",
        True,
    )
    again = await analyst.client.post(major_url, json={"reason": "again"})
    assert "already declared" in again.text
    assert (await analyst.client.post(major_url, json={"reason": " "})).status_code == 422
    assert "case.major_incident" in await audit_actions(owner, f"user:{analyst.id}")

    other = (await create(analyst.client, tenant))["number"]
    await close_case(analyst.client, tenant, other)
    closed = await analyst.client.post(
        url(tenant, f"/{other}/major-incident"), json={"reason": "x"}
    )
    assert "closed cases cannot be declared" in closed.text


async def test_merge_moves_tasks(analyst: SignedIn, tenant: str) -> None:
    source = await create(analyst.client, tenant)
    target = await create(analyst.client, tenant)

    merged = await analyst.client.post(
        url(tenant, f"/{source['number']}/merge"), json={"into": target["number"]}
    )

    assert merged.status_code == 200, merged.text
    moved = [t for t in merged.json()["tasks"] if t["playbook_key"] is None]
    assert sorted(t["id"] for t in moved) == sorted(t["id"] for t in source["tasks"])
    assert len(merged.json()["tasks"]) == 8
