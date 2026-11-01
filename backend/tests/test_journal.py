import secrets
from typing import Any

import pytest
from httpx2 import AsyncClient
from pytest import MonkeyPatch
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app import escalation
from app.db import SessionLocal
from app.journal import mentions
from app.models import Tenant
from tests.conftest import MakeUser, SignedIn, audit_actions

XSS = '<script>alert(1)</script> <img src=x onerror="alert(2)"> [click](javascript:alert(3))'


@pytest.fixture
async def analyst(make_user: MakeUser, tenant: str) -> SignedIn:
    return await make_user({tenant: ["analyst"]})


async def new_case(client: AsyncClient, tenant: str) -> int:
    response = await client.post(
        f"/api/tenants/{tenant}/cases", json={"template": "ddos", "title": "x"}
    )
    assert response.status_code == 201, response.text
    return response.json()["number"]


def url(tenant: str, number: int, suffix: str = "") -> str:
    return f"/api/tenants/{tenant}/cases/{number}{suffix}"


async def post(client: AsyncClient, path: str, body: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(path, json=body)
    assert response.status_code == 201, response.text
    return response.json()


# --- Timeline -----------------------------------------------------------------------------


async def test_timeline_is_ordered_by_when_events_happened(
    analyst: SignedIn, tenant: str, owner: AsyncEngine
) -> None:
    number = await new_case(analyst.client, tenant)
    events = url(tenant, number, "/timeline")
    await post(
        analyst.client,
        events,
        {
            "occurred_at": "2026-10-30T12:00:00Z",
            "source": "EDR",
            "description": "Beacon observed",
            "milestone": "detection",
        },
    )
    case = await post(
        analyst.client,
        events,
        {
            "occurred_at": "2026-10-28T08:15:00+02:00",
            "source": "Proxy log",
            "description": "First phish click",
            "milestone": "first_compromise",
        },
    )

    timeline = case["timeline"]
    assert [e["description"] for e in timeline] == ["First phish click", "Beacon observed"]
    first = timeline[0]
    assert first["occurred_at"] == "2026-10-28T06:15:00Z"
    assert first["recorded_at"] > first["occurred_at"]
    assert first["recorded_by"] == f"user:{analyst.id}"
    assert "timeline.create" in await audit_actions(owner, f"user:{analyst.id}")


async def test_timeline_links_and_milestones_are_checked(analyst: SignedIn, tenant: str) -> None:
    number = await new_case(analyst.client, tenant)
    other = await new_case(analyst.client, tenant)
    linked = await analyst.client.post(
        url(tenant, number, "/observables"),
        json={"observables": [{"type": "ipv4", "value": "203.0.113.7"}]},
    )
    observable = linked.json()["observables"][0]["id"]
    asset = (
        await analyst.client.post(url(tenant, other, "/assets"), json={"kind": "host", "name": "h"})
    ).json()["id"]
    base = {"occurred_at": "2026-10-30T12:00:00Z", "source": "EDR", "description": "x"}
    events = url(tenant, number, "/timeline")

    case = await post(analyst.client, events, base | {"observable_id": observable})
    assert case["timeline"][0]["observable_id"] == observable
    await post(analyst.client, events, base | {"milestone": "containment"})

    bad = [
        (base | {"asset_id": asset}, "the asset must belong to this case"),
        (base | {"evidence_id": "00000000-0000-0000-0000-000000000000"}, "evidence must belong"),
        (base | {"milestone": "containment"}, "already has a containment milestone"),
        (base | {"milestone": "lunch"}, "Input should be"),
        (base | {"description": " "}, "at least 1 character"),
        (base | {"occurred_at": "2026-10-30T12:00:00"}, "timezone"),
    ]
    for body, message in bad:
        response = await analyst.client.post(events, json=body)
        assert response.status_code == 422
        assert message in response.text


async def test_timeline_events_can_be_deleted(analyst: SignedIn, tenant: str) -> None:
    number = await new_case(analyst.client, tenant)
    case = await post(
        analyst.client,
        url(tenant, number, "/timeline"),
        {"occurred_at": "2026-10-30T12:00:00Z", "source": "EDR", "description": "x"},
    )
    event_url = url(tenant, number, f"/timeline/{case['timeline'][0]['id']}")
    assert (await analyst.client.delete(event_url)).status_code == 204
    assert (await analyst.client.delete(event_url)).status_code == 404


# --- Notes --------------------------------------------------------------------------------


async def test_note_markdown_is_stored_verbatim_and_never_rendered(
    analyst: SignedIn, tenant: str
) -> None:
    number = await new_case(analyst.client, tenant)
    body = f"## Findings\n\n```\npowershell -enc AAAA\n```\n\n{XSS}"

    response = await analyst.client.post(url(tenant, number, "/notes"), json={"body": body})

    assert response.headers["content-type"] == "application/json"
    note = response.json()["notes"][0]
    assert note["body"] == body  # stored as Markdown; the API never returns HTML
    assert (note["revision"], note["privileged"]) == (1, False)


async def test_every_edit_is_a_revision(
    analyst: SignedIn, make_user: MakeUser, tenant: str, owner: AsyncEngine
) -> None:
    lead = await make_user({tenant: ["ir_lead"]})
    number = await new_case(analyst.client, tenant)
    created = await post(analyst.client, url(tenant, number, "/notes"), {"body": "Draft"})
    note_url = url(tenant, number, f"/notes/{created['notes'][0]['id']}")

    await analyst.client.patch(note_url, json={"body": "Draft, expanded"})
    unchanged = await analyst.client.patch(note_url, json={"body": "Draft, expanded"})
    assert unchanged.json()["notes"][0]["revision"] == 2
    final = await lead.client.patch(note_url, json={"body": "Final", "privileged": True})
    note = final.json()["notes"][0]
    assert (note["body"], note["revision"], note["privileged"]) == ("Final", 3, True)
    assert note["created_by"] == f"user:{analyst.id}"
    assert note["updated_by"] == f"user:{lead.id}"

    history = (await analyst.client.get(f"{note_url}/revisions")).json()
    assert [(r["revision"], r["body"], r["privileged"], r["edited_by"]) for r in history] == [
        (1, "Draft", False, f"user:{analyst.id}"),
        (2, "Draft, expanded", False, f"user:{analyst.id}"),
        (3, "Final", True, f"user:{lead.id}"),
    ]
    assert "note.update" in await audit_actions(owner, f"user:{lead.id}")


@pytest.mark.parametrize(
    "statement",
    ["UPDATE note_revisions SET body = 'x'", "DELETE FROM note_revisions"],
)
async def test_revisions_are_append_only(owner: AsyncEngine, statement: str) -> None:
    async with SessionLocal() as db:
        with pytest.raises(DBAPIError, match="permission denied"):
            await db.execute(text(statement))
    with pytest.raises(DBAPIError, match="append-only"):
        async with owner.begin() as conn:
            await conn.execute(text(statement))


async def test_unknown_notes(analyst: SignedIn, tenant: str) -> None:
    number = await new_case(analyst.client, tenant)
    missing = url(tenant, number, "/notes/00000000-0000-0000-0000-000000000000")
    assert (await analyst.client.patch(missing, json={"body": "x"})).status_code == 404
    assert (await analyst.client.get(f"{missing}/revisions")).status_code == 404


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("ping @jdoe and @a.smith-2.", {"jdoe", "a.smith-2"}),
        ("email jdoe@example.com is not a mention", set()),
        ("@@double and @_bad and @", set()),
        ("(@jdoe), @jdoe again", {"jdoe"}),
    ],
)
def test_mentions(body: str, expected: set[str]) -> None:
    assert mentions(body) == expected


@pytest.fixture
def outbox(monkeypatch: MonkeyPatch) -> list[tuple[list[str], dict[str, Any]]]:
    sent: list[tuple[list[str], dict[str, Any]]] = []
    monkeypatch.setattr(escalation, "send_email", lambda to, c: sent.append((to, c)))
    monkeypatch.setattr(escalation, "post_webhook", lambda u, c: None)
    return sent


async def deliver(owner: AsyncEngine, slug: str) -> None:
    async with owner.connect() as conn:
        tenant_id = await conn.scalar(text("SELECT id FROM tenants WHERE slug = :s"), {"s": slug})
    await escalation.deliver(Tenant(id=tenant_id, slug=slug))


async def test_mentions_notify_only_users_who_can_see_the_case(
    make_user: MakeUser, tenant: str, owner: AsyncEngine, outbox: list[Any]
) -> None:
    run = secrets.token_hex(3)
    insider = await make_user(
        {tenant: ["analyst", "restricted_case"]}, username=f"ins{run}", email="insider@example.com"
    )
    await make_user({tenant: ["analyst"]}, username=f"ana{run}", email="analyst@example.com")
    await make_user({"insider-threat": ["admin"]}, username=f"out{run}", email="out@example.com")
    await make_user({tenant: ["analyst", "restricted_case"]}, username=f"nomail{run}")  # fmt: skip
    number = await new_case(insider.client, tenant)
    await insider.client.patch(url(tenant, number), json={"restricted": True})
    body = f"@ins{run} @ana{run} @out{run} @nomail{run} @nobody please review"

    created = await post(insider.client, url(tenant, number, "/notes"), {"body": body})
    note_url = url(tenant, number, f"/notes/{created['notes'][0]['id']}")
    await insider.client.patch(note_url, json={"body": body + " (updated)"})
    await deliver(owner, tenant)

    assert [to for to, _ in outbox] == [["insider@example.com"]]
    assert outbox[0][1]["summary"] == f"Case #{number} (high): you were mentioned in a note"
    assert body not in str(outbox)


async def test_mentions_are_notified_once_and_on_new_mentions(
    make_user: MakeUser, analyst: SignedIn, tenant: str, owner: AsyncEngine, outbox: list[Any]
) -> None:
    run = secrets.token_hex(3)
    await make_user({tenant: ["analyst"]}, username=f"first{run}", email="first@example.com")
    await make_user({tenant: ["ir_lead"]}, username=f"second{run}", email="second@example.com")
    number = await new_case(analyst.client, tenant)
    created = await post(analyst.client, url(tenant, number, "/notes"), {"body": f"@first{run}"})
    note_url = url(tenant, number, f"/notes/{created['notes'][0]['id']}")

    await analyst.client.patch(note_url, json={"body": f"@first{run} again"})
    await analyst.client.patch(note_url, json={"body": f"@first{run} and @second{run}"})
    await deliver(owner, tenant)

    assert sorted(to[0] for to, _ in outbox) == ["first@example.com", "second@example.com"]
    actions = await audit_actions(owner, f"user:{analyst.id}")
    assert actions.count("note.update") == 2


# --- Comments and privilege ---------------------------------------------------------------


async def test_comments_form_a_stream(analyst: SignedIn, make_user: MakeUser, tenant: str) -> None:
    lead = await make_user({tenant: ["ir_lead"]})
    number = await new_case(analyst.client, tenant)
    await post(analyst.client, url(tenant, number, "/comments"), {"body": "Isolating web-01 now"})
    case = await post(lead.client, url(tenant, number, "/comments"), {"body": "Ack"})

    assert [(c["body"], c["author"]) for c in case["comments"]] == [
        ("Isolating web-01 now", f"user:{analyst.id}"),
        ("Ack", f"user:{lead.id}"),
    ]
    empty = await analyst.client.post(url(tenant, number, "/comments"), json={"body": "  "})
    assert empty.status_code == 422
    async with SessionLocal() as db:
        with pytest.raises(DBAPIError, match="permission denied"):
            await db.execute(text("UPDATE comments SET body = 'edited'"))


async def test_privilege_is_set_by_legal(
    analyst: SignedIn, make_user: MakeUser, tenant: str, owner: AsyncEngine
) -> None:
    counsel = await make_user({tenant: ["legal"]})
    number = await new_case(analyst.client, tenant)
    marking = {"on": True, "reason": "Prepared at direction of outside counsel"}

    assert (
        await analyst.client.put(url(tenant, number, "/privilege"), json=marking)
    ).status_code == 403
    marked = await counsel.client.put(url(tenant, number, "/privilege"), json=marking)

    assert marked.json()["privileged"] is True
    assert "case.privilege" in await audit_actions(owner, f"user:{counsel.id}")
    # Legal can read but not write the case.
    blocked = await counsel.client.post(url(tenant, number, "/notes"), json={"body": "x"})
    assert blocked.status_code == 403


async def test_merge_moves_the_journal(analyst: SignedIn, tenant: str) -> None:
    source = await new_case(analyst.client, tenant)
    target = await new_case(analyst.client, tenant)
    event = {"occurred_at": "2026-10-30T12:00:00Z", "source": "EDR", "description": "x",
             "milestone": "detection"}  # fmt: skip
    await post(analyst.client, url(tenant, source, "/timeline"), event)
    await post(analyst.client, url(tenant, target, "/timeline"), event | {"description": "y"})
    await post(analyst.client, url(tenant, source, "/notes"), {"body": "note"})
    await post(analyst.client, url(tenant, source, "/comments"), {"body": "comment"})

    merged = await analyst.client.post(url(tenant, source, "/merge"), json={"into": target})

    assert merged.status_code == 200, merged.text
    case = merged.json()
    assert sorted((e["description"], e["milestone"]) for e in case["timeline"]) == [
        ("x", None),  # the target keeps its own detection milestone
        ("y", "detection"),
    ]
    assert [n["body"] for n in case["notes"]] == ["note"]
    assert [c["body"] for c in case["comments"]] == ["comment"]
