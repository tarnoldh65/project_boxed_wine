from typing import Any

import pytest
from botocore.exceptions import ClientError
from httpx2 import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app import storage
from app.config import settings
from app.db import SessionLocal, set_tenant
from app.jobs import retention
from tests.conftest import MakeUser, SignedIn, close_case
from tests.test_escalation import tenant_row
from tests.test_evidence import upload_sample

# Every table that holds a case's data, with how its rows find the case.
CASE_DATA = {
    "alerts": "case_id", "case_assets": "case_id", "tasks": "case_id", "notes": "case_id",
    "comments": "case_id", "timeline_events": "case_id", "evidence": "case_id",
    "case_observables": "case_id", "pirs": "case_id", "pir_actions": "case_id",
    "regulatory_clocks": "case_id", "materiality_decisions": "case_id", "disclosures": "case_id",
    "notifications": "case_id",
    "note_revisions": "note_id IN (SELECT id FROM notes WHERE case_id",
    "custody_log": "evidence_id IN (SELECT id FROM evidence WHERE case_id",
}  # fmt: skip


@pytest.fixture
async def admin(make_user: MakeUser, tenant: str) -> SignedIn:
    """Admins hold every case permission, legal hold included."""
    return await make_user({tenant: ["admin", "malware:read"]})


async def call(client: AsyncClient, method: str, path: str, body: Any, status: int = 201) -> Any:
    response = await client.request(method, path, json=body)
    assert response.status_code == status, response.text
    return response.json()


async def keep_phishing_for(client: AsyncClient, tenant: str, days: int) -> None:
    config = await call(client, "GET", f"/api/tenants/{tenant}/config", None, 200)
    templates = config["templates"]
    next(t for t in templates if t["key"] == "phishing")["retention_days"] = days
    await call(client, "PUT", f"/api/tenants/{tenant}/config/templates", templates, 200)


async def full_case(client: AsyncClient, tenant: str, owner: AsyncEngine) -> dict[str, Any]:
    """A closed phishing case with data in every table that holds case data."""
    base = f"/api/tenants/{tenant}/cases"
    number = (await call(client, "POST", base, {"template": "phishing", "title": "x"}))["number"]
    url = f"{base}/{number}"
    await call(client, "PATCH", url, {"regulated_data": ["pii"], "data_regions": ["EU"]}, 200)
    await call(client, "POST", f"{url}/notes", {"body": "Note"})
    await call(client, "POST", f"{url}/comments", {"body": "Comment"})
    await call(client, "POST", f"{url}/assets", {"kind": "host", "name": "pc-1"})
    await call(client, "POST", f"{url}/observables",
               {"observables": [{"type": "domain", "value": "kept.example"}]})  # fmt: skip
    sample = await upload_sample(client, tenant, number)
    assert sample.status_code == 201, sample.text
    case = await call(client, "PUT", f"{url}/pir", {"root_cause": "x"}, 200)
    clock = case["clocks"][0]["id"]
    await call(client, "POST", f"{url}/timeline", {"occurred_at": "2026-10-01T00:00:00Z",
               "source": "s", "description": "d", "evidence_id": sample.json()["id"]})  # fmt: skip
    await call(client, "POST", f"{url}/pir/actions", {"title": "Fix"})
    await call(client, "POST", f"{url}/materiality", {"material": False, "decided_by": "GC",
               "decided_at": "2026-10-01T00:00:00Z", "rationale": "r"})  # fmt: skip
    await call(client, "POST", f"{url}/disclosures", {"recipient_type": "regulator",
               "recipient": "DPC", "sent_at": "2026-10-02T00:00:00Z", "summary": "s",
               "clock_id": clock})  # fmt: skip
    await close_case(client, tenant, number, "benign")
    case = await call(client, "GET", url, None, 200)
    async with owner.begin() as conn:
        for insert in (
            "INSERT INTO alerts (tenant_id, case_id, source, title, payload) "
            "SELECT tenant_id, id, 'edr', 't', '{}' FROM cases WHERE id = :id",
            "INSERT INTO notifications (tenant_id, case_id, rule, trigger, step, emails) "
            "SELECT tenant_id, id, 'r', 'case_created', '', '{}' FROM cases WHERE id = :id",
        ):
            await conn.execute(text(insert), {"id": case["id"]})
    return case


async def closed_days_ago(owner: AsyncEngine, case_id: str, days: int) -> None:
    async with owner.begin() as conn:
        await conn.execute(
            text("UPDATE cases SET closed_at = now() - make_interval(days => :d) WHERE id = :id"),
            {"d": days, "id": case_id},
        )


async def rows(owner: AsyncEngine, case_id: str) -> dict[str, int]:
    found = {}
    async with owner.connect() as conn:
        for table, match in CASE_DATA.items():
            close = ")" if "(" in match else ""
            found[table] = await conn.scalar(
                text(f"SELECT count(*) FROM {table} WHERE {match} = :id{close}"),  # noqa: S608
                {"id": case_id},
            )
        found["cases"] = await conn.scalar(
            text("SELECT count(*) FROM cases WHERE id = :id"), {"id": case_id}
        )
    return found


def stored(key: str) -> bool:
    try:
        storage.client().head_object(Bucket=settings.s3_bucket, Key=key)
    except ClientError:
        return False
    return True


async def object_key(owner: AsyncEngine, evidence_id: str) -> str:
    async with owner.connect() as conn:
        key = await conn.scalar(
            text("SELECT object_key FROM evidence WHERE id = :id"), {"id": evidence_id}
        )
    assert key
    return key


async def test_expired_cases_are_purged_with_their_evidence(
    admin: SignedIn, tenant: str, owner: AsyncEngine
) -> None:
    await keep_phishing_for(admin.client, tenant, 30)
    expired = await full_case(admin.client, tenant, owner)
    recent = await full_case(admin.client, tenant, owner)
    key = await object_key(owner, expired["evidence"][0]["id"])
    before = await rows(owner, expired["id"])
    assert all(before.values()), before
    await closed_days_ago(owner, expired["id"], 31)
    await closed_days_ago(owner, recent["id"], 29)

    assert await retention.purge_tenant(await tenant_row(owner, tenant)) == 1

    assert set((await rows(owner, expired["id"])).values()) == {0}
    assert not stored(key)
    assert all((await rows(owner, recent["id"])).values())
    async with owner.connect() as conn:
        detail = await conn.scalar(
            text("SELECT detail FROM audit_log WHERE action = 'case.purge' AND target = :t "
                 "AND tenant_id = (SELECT id FROM tenants WHERE slug = :slug)"),
            {"t": f"case:{expired['number']}", "slug": tenant},
        )  # fmt: skip
        observables = await conn.scalar(
            text("SELECT count(*) FROM observables WHERE value = 'kept.example'")
        )
    assert detail["category"] == "phishing"
    assert detail["retention_days"] == 30
    assert detail["evidence_objects"] == 1
    assert observables  # shared observables stay; only the case's links go


async def test_purge_respects_legal_hold(admin: SignedIn, tenant: str, owner: AsyncEngine) -> None:
    await keep_phishing_for(admin.client, tenant, 1)
    held = await full_case(admin.client, tenant, owner)
    await call(admin.client, "PUT", f"/api/tenants/{tenant}/cases/{held['number']}/legal-hold",
               {"on": True, "reason": "Litigation"}, 200)  # fmt: skip
    await closed_days_ago(owner, held["id"], 400)

    assert await retention.purge_tenant(await tenant_row(owner, tenant)) == 0
    assert all((await rows(owner, held["id"])).values())

    # The database refuses too, whatever the caller decided.
    async with SessionLocal() as db:
        await set_tenant(db, await _tenant_id(owner, tenant))
        with pytest.raises(DBAPIError, match="under legal hold"):
            await db.execute(text("SELECT purge_case(:id)"), {"id": held["id"]})


async def _tenant_id(owner: AsyncEngine, slug: str) -> Any:
    return (await tenant_row(owner, slug)).id


async def test_open_cases_and_types_without_retention_are_kept(
    admin: SignedIn, tenant: str, owner: AsyncEngine
) -> None:
    base = f"/api/tenants/{tenant}/cases"
    open_case = await call(admin.client, "POST", base, {"template": "phishing", "title": "x"})
    other = await call(admin.client, "POST", base, {"template": "ddos", "title": "x"})
    await close_case(admin.client, tenant, other["number"], "benign")
    await closed_days_ago(owner, other["id"], 4000)
    await keep_phishing_for(admin.client, tenant, 1)

    assert await retention.purge_tenant(await tenant_row(owner, tenant)) == 0
    async with SessionLocal() as db:
        await set_tenant(db, await _tenant_id(owner, tenant))
        with pytest.raises(DBAPIError, match="is open"):
            await db.execute(text("SELECT purge_case(:id)"), {"id": open_case["id"]})
    assert await retention.run() == 0


@pytest.mark.parametrize("table", ["note_revisions", "custody_log"])
async def test_the_app_cannot_delete_history_outside_a_purge(table: str) -> None:
    async with SessionLocal() as db:
        await db.execute(text("SELECT set_config('app.purging', 'on', true)"))
        with pytest.raises(DBAPIError, match="permission denied"):
            await db.execute(text(f"DELETE FROM {table}"))  # noqa: S608
