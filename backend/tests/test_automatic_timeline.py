from typing import Any

import pytest
from httpx2 import AsyncClient

from tests.conftest import MakeToken, MakeUser, SignedIn

COLLECTION = {"collected_by": "J Doe", "collected_at": "2026-10-31T09:00:00Z"}


@pytest.fixture
async def lead(make_user: MakeUser, tenant: str) -> SignedIn:
    return await make_user({tenant: ["ir_lead"]})


def url(tenant: str, number: int, suffix: str = "") -> str:
    return f"/api/tenants/{tenant}/cases/{number}{suffix}"


async def call(client: AsyncClient, method: str, path: str, body: Any = None) -> dict[str, Any]:
    response = await client.request(method, path, json=body)
    assert response.status_code < 300, response.text
    return response.json() if response.content else {}


async def recorded(client: AsyncClient, tenant: str, number: int) -> list[str]:
    case = await call(client, "GET", url(tenant, number))
    return [e["description"] for e in case["timeline"] if e["automatic"]]


async def new_case(client: AsyncClient, tenant: str, title: str = "x") -> int:
    case = await call(
        client, "POST", f"/api/tenants/{tenant}/cases", {"template": "ddos", "title": title}
    )
    return case["number"]


async def test_workflow_actions_go_on_the_timeline(lead: SignedIn, tenant: str) -> None:
    client = lead.client
    number = await new_case(client, tenant)
    case = url(tenant, number)

    await call(client, "POST", f"{case}/acknowledge")
    await call(client, "PATCH", case, {"state": "triage", "impact": "high", "urgency": "high"})
    await call(
        client, "PATCH", case, {"owner_id": None, "title": "Flood", "description": "SYN flood"}
    )
    await call(client, "PATCH", case, {"regulated_data": ["pii"], "data_regions": ["EU"]})
    await call(client, "POST", f"{case}/major-incident", {"reason": "Checkout is down"})
    await call(client, "PUT", f"{case}/legal-hold", {"on": True, "reason": "Litigation memo 7"})
    await call(client, "PUT", f"{case}/privilege", {"on": True, "reason": "Counsel engaged"})
    await call(client, "PUT", f"{case}/privilege", {"on": False, "reason": "Released"})
    await call(client, "PUT", f"{case}/legal-hold", {"on": False, "reason": "Settled"})

    lines = await recorded(client, tenant, number)
    assert lines == [
        "Case opened from the DDoS template",
        "Acknowledged",
        "State changed from New to Triage",
        "Severity changed from high to critical",
        "Unassigned",
        "Details edited: description, title",
        "Regulated data: PII (EU)",
        "Notification clock started: GDPR Art. 33 (supervisory authority)",
        "Major incident declared: Checkout is down",
        "Legal hold placed",
        "Marked privileged",
        "Privilege marking removed",
        "Legal hold lifted",
    ]
    # Reasons for legal hold and privilege stay in the audit log.
    assert not any("Litigation" in line or "Counsel" in line for line in lines)


async def test_closing_records_the_resolution(lead: SignedIn, tenant: str) -> None:
    client = lead.client
    number = await new_case(client, tenant)
    case = await call(client, "GET", url(tenant, number))
    for task in case["tasks"]:
        await call(client, "PATCH", url(tenant, number, f"/tasks/{task['id']}"),
                   {"status": "waived", "waive_reason": "Not needed"})  # fmt: skip
    await call(client, "PATCH", url(tenant, number), {"state": "closed", "resolution": "benign"})

    lines = await recorded(client, tenant, number)
    assert lines[-1] == "State changed from New to Closed, resolved as benign"
    assert f"Task waived: {case['tasks'][0]['title']} (Not needed)" in lines


async def test_task_progress_goes_on_the_timeline(lead: SignedIn, tenant: str) -> None:
    client = lead.client
    number = await new_case(client, tenant)
    task = (await call(client, "POST", url(tenant, number, "/tasks"), {"title": "Block ASN"}))[
        "tasks"
    ][-1]
    path = url(tenant, number, f"/tasks/{task['id']}")
    await call(client, "PATCH", path, {"status": "in_progress"})
    await call(client, "PATCH", path, {"status": "done"})
    await call(client, "PATCH", path, {"status": "open"})
    await call(client, "PATCH", path, {"assignee_id": None})

    lines = await recorded(client, tenant, number)
    assert lines[-4:] == [
        "Task added: Block ASN",
        "Task started: Block ASN",
        "Task done: Block ASN",
        "Task reopened: Block ASN",
    ]


async def test_journal_entries_go_on_the_timeline_without_their_text(
    lead: SignedIn, tenant: str
) -> None:
    client = lead.client
    number = await new_case(client, tenant)
    note = (await call(client, "POST", url(tenant, number, "/notes"),
                       {"body": "Secret finding", "privileged": True}))["notes"][0]  # fmt: skip
    await call(client, "POST", url(tenant, number, "/notes"), {"body": "Plain"})
    await call(client, "PATCH", url(tenant, number, f"/notes/{note['id']}"),
               {"body": "Secret finding, revised", "privileged": True})  # fmt: skip
    await call(client, "POST", url(tenant, number, "/comments"), {"body": "On it"})

    lines = await recorded(client, tenant, number)
    assert lines[-4:] == ["Privileged note added", "Note added", "Note edited", "Comment added"]


async def test_observables_assets_and_evidence_are_linked_from_their_entries(
    lead: SignedIn, tenant: str
) -> None:
    client = lead.client
    number = await new_case(client, tenant)
    case = await call(client, "POST", url(tenant, number, "/observables"),
                      {"observables": [{"type": "domain", "value": "evil.example"}]})  # fmt: skip
    observable = case["observables"][0]["id"]
    # Linking again adds no entry.
    await call(client, "POST", url(tenant, number, "/observables"),
               {"observables": [{"type": "domain", "value": "evil.example"}]})  # fmt: skip
    await call(client, "DELETE", url(tenant, number, f"/observables/{observable}"))
    asset = (await call(client, "POST", url(tenant, number, "/assets"),
                        {"kind": "data_set", "name": "payroll"}))["id"]  # fmt: skip
    await call(client, "DELETE", url(tenant, number, f"/assets/{asset}"))
    reference = {"name": "Laptop image", "location": "Locker 3", "collection": COLLECTION}
    evidence = await call(client, "POST", url(tenant, number, "/evidence/references"), reference)
    await call(client, "POST", url(tenant, number, f"/evidence/{evidence['id']}/transfer"),
               {"to": "Counsel", "reason": "Review"})  # fmt: skip
    response = await client.delete(
        url(tenant, number, f"/evidence/{evidence['id']}"), params={"reason": "Duplicate"}
    )
    assert response.status_code == 204, response.text

    timeline = (await call(client, "GET", url(tenant, number)))["timeline"]
    entries = {e["description"]: e for e in timeline if e["automatic"]}
    assert entries["Observable linked: domain evil[.]example"]["observable_id"] == observable
    assert entries["Observable unlinked: domain evil[.]example"]["observable_id"] == observable
    assert entries["Asset linked: data set payroll"]["asset_id"] == asset
    assert "Asset unlinked: data set payroll" in entries
    assert (
        entries["Evidence recorded, held elsewhere: Laptop image"]["evidence_id"] == evidence["id"]
    )
    assert "Evidence Laptop image transferred to Counsel" in entries
    assert "Evidence deleted: Laptop image (Duplicate)" in entries
    assert [e["description"] for e in timeline].count(
        "Observable linked: domain evil[.]example"
    ) == 1


async def test_review_and_regulatory_work_go_on_the_timeline(
    make_user: MakeUser, tenant: str
) -> None:
    client = (await make_user({tenant: ["ir_lead", "legal"]})).client
    number = await new_case(client, tenant)
    await call(client, "PUT", url(tenant, number, "/pir"), {"root_cause": "Exposed admin port"})
    action = (await call(client, "POST", url(tenant, number, "/pir/actions"),
                         {"title": "Close the port"}))["pir"]["actions"][0]  # fmt: skip
    await call(client, "PATCH", url(tenant, number, f"/pir/actions/{action['id']}"), {"done": True})
    await call(client, "POST", url(tenant, number, "/materiality"),
               {"material": True, "decided_by": "Disclosure committee",
                "decided_at": "2026-11-18T10:00:00Z", "rationale": "Revenue impact"})  # fmt: skip
    await call(client, "POST", url(tenant, number, "/disclosures"),
               {"recipient_type": "regulator", "recipient": "SEC",
                "sent_at": "2026-11-19T10:00:00Z", "summary": "Form 8-K"})  # fmt: skip

    lines = await recorded(client, tenant, number)
    assert lines[1:] == [
        "Post-incident review updated",
        "Review action added: Close the port",
        "Review action done: Close the port",
        "Materiality decided by Disclosure committee: material or reportable",
        "Notification clock started: SEC Form 8-K Item 1.05",
        "Notification sent to SEC (regulator)",
    ]


async def test_alerts_merges_and_splits_go_on_the_timeline(
    lead: SignedIn, tenant: str, make_token: MakeToken, client: AsyncClient
) -> None:
    token = await make_token(tenant, ["alerts:write"])
    alert = await client.post(
        f"/api/tenants/{tenant}/alerts",
        json={"template": "ransomware", "title": "Encryption burst", "source": "edr"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert alert.status_code == 201, alert.text
    source = alert.json()["case_number"]
    assert await recorded(lead.client, tenant, source) == [
        "Case opened from the Ransomware template",
        "Alert received from edr: Encryption burst",
    ]

    alert_id = (await call(lead.client, "GET", url(tenant, source)))["alerts"][0]["id"]
    split = await call(lead.client, "POST", url(tenant, source, "/split"),
                       {"title": "Second wave", "alert_ids": [alert_id]})  # fmt: skip
    assert (await recorded(lead.client, tenant, source))[
        -1
    ] == f"Split into case #{split['number']}"
    assert await recorded(lead.client, tenant, split["number"]) == [f"Split from case #{source}"]

    target = await new_case(lead.client, tenant)
    await call(lead.client, "POST", url(tenant, split["number"], "/merge"), {"into": target})
    merged = await recorded(lead.client, tenant, target)
    # The merged case's entries move with it, then both sides record the merge.
    assert f"Split from case #{source}" in merged
    assert merged[-1] == f"Case #{split['number']} merged into this case"
    assert (await recorded(lead.client, tenant, split["number"])) == [f"Merged into case #{target}"]


async def test_entries_the_app_recorded_cannot_be_removed(lead: SignedIn, tenant: str) -> None:
    number = await new_case(lead.client, tenant)
    case = await call(lead.client, "GET", url(tenant, number))
    automatic = next(e for e in case["timeline"] if e["automatic"])
    assert automatic["source"] == "Case Manager"
    assert automatic["recorded_by"] == f"user:{lead.id}"

    response = await lead.client.delete(url(tenant, number, f"/timeline/{automatic['id']}"))
    assert response.status_code == 422
    assert "cannot be removed" in response.text
    manual = await call(lead.client, "POST", url(tenant, number, "/timeline"),
                        {"occurred_at": "2026-11-01T10:00:00Z", "source": "EDR",
                         "description": "Beacon"})  # fmt: skip
    entry = next(e for e in manual["timeline"] if not e["automatic"])
    removed = await lead.client.delete(url(tenant, number, f"/timeline/{entry['id']}"))
    assert removed.status_code == 204
