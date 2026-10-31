import hashlib
import io
from typing import Any

import httpx2
import pytest
import pyzipper
from httpx2 import AsyncClient
from pytest import MonkeyPatch
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app import storage
from app.config import settings
from app.db import SessionLocal
from app.evidence import content_disposition
from app.jobs import evidence_hash
from tests.conftest import MakeUser, SignedIn, audit_actions

COLLECTION = {
    "collected_by": "J Doe",
    "collected_at": "2026-10-31T09:00:00Z",
    "collected_from": "web-01",
    "method": "EDR live response",
}


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
    return f"/api/tenants/{tenant}/cases/{number}/evidence{suffix}"


async def start(client: AsyncClient, tenant: str, number: int, size: int, name: str = "disk.img"):
    response = await client.post(
        url(tenant, number, "/uploads"),
        json={"name": name, "size": size, "collection": COLLECTION},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def upload(client: AsyncClient, tenant: str, number: int, data: bytes, **kw: Any) -> str:
    """Upload the way a browser does: every part straight to S3, then complete."""
    started = await start(client, tenant, number, len(data), **kw)
    size = started["part_size"]
    for i, part_url in enumerate(started["part_urls"]):
        response = httpx2.put(part_url, content=data[i * size : (i + 1) * size], timeout=30)
        assert response.status_code == 200, response.text
    evidence_id = started["evidence"]["id"]
    done = await client.post(url(tenant, number, f"/{evidence_id}/complete"))
    assert done.status_code == 200, done.text
    return evidence_id


async def report(client: AsyncClient, tenant: str, number: int, evidence_id: str):
    response = await client.get(url(tenant, number, f"/{evidence_id}/custody"))
    assert response.status_code == 200, response.text
    return response.json()


def actions(custody: dict[str, Any]) -> list[str]:
    return [e["action"] for e in custody["entries"]]


async def test_multipart_upload_is_hashed_and_verified_on_download(
    analyst: SignedIn, tenant: str, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "s3_part_size_mb", 5)  # S3's minimum part size
    data = bytes(range(256)) * (11 * 4096)  # 11 MiB: three parts
    number = await new_case(analyst.client, tenant)

    started = await start(analyst.client, tenant, number, len(data))
    assert started["part_size"] == 5 * storage.MB
    assert len(started["part_urls"]) == 3
    await analyst.client.post(url(tenant, number, f"/{started['evidence']['id']}/abort"))
    evidence_id = await upload(analyst.client, tenant, number, data)

    case = (await analyst.client.get(f"/api/tenants/{tenant}/cases/{number}")).json()
    item = next(e for e in case["evidence"] if e["id"] == evidence_id)
    assert (item["status"], item["size"], item["sha256"]) == ("stored", len(data), None)

    assert await evidence_hash.run() == 0
    custody = await report(analyst.client, tenant, number, evidence_id)
    assert custody["evidence"]["sha256"] == hashlib.sha256(data).hexdigest()
    assert custody["evidence"]["status"] == "hashed"

    response = await analyst.client.get(url(tenant, number, f"/{evidence_id}/download"))
    assert response.status_code == 200
    assert response.content == data
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith('attachment; filename="disk.img"')

    custody = await report(analyst.client, tenant, number, evidence_id)
    assert actions(custody) == ["collected", "uploaded", "hashed", "accessed", "verified"]
    assert custody["entries"][0]["detail"] == COLLECTION
    assert custody["chain_intact"] is True


async def test_upload_must_be_complete(analyst: SignedIn, tenant: str) -> None:
    number = await new_case(analyst.client, tenant)
    started = await start(analyst.client, tenant, number, 100)
    evidence_id = started["evidence"]["id"]

    response = await analyst.client.post(url(tenant, number, f"/{evidence_id}/complete"))
    assert response.status_code == 422
    assert "the upload is incomplete" in response.text

    assert (
        await analyst.client.post(url(tenant, number, f"/{evidence_id}/abort"))
    ).status_code == 204
    again = await analyst.client.post(url(tenant, number, f"/{evidence_id}/complete"))
    assert "not in progress" in again.text
    custody = await report(analyst.client, tenant, number, evidence_id)
    assert (custody["evidence"]["status"], actions(custody)) == (
        "deleted",
        ["collected", "deleted"],
    )


async def test_tampered_object_is_detected_on_download(
    analyst: SignedIn, tenant: str, owner: AsyncEngine
) -> None:
    number = await new_case(analyst.client, tenant)
    evidence_id = await upload(analyst.client, tenant, number, b"original log")
    await evidence_hash.run()
    async with owner.connect() as conn:
        key = await conn.scalar(
            text("SELECT object_key FROM evidence WHERE id = :id"), {"id": evidence_id}
        )
    storage.put(key, b"tampered log")

    response = await analyst.client.get(url(tenant, number, f"/{evidence_id}/download"))

    assert response.content == b"tampered log"
    custody = await report(analyst.client, tenant, number, evidence_id)
    assert custody["evidence"]["status"] == "mismatch"
    mismatch = custody["entries"][-1]
    assert mismatch["action"] == "mismatch"
    assert mismatch["detail"]["actual"] == hashlib.sha256(b"tampered log").hexdigest()
    assert "evidence.mismatch" in await audit_actions(owner, f"user:{analyst.id}")


async def test_download_before_hashing_records_access_only(analyst: SignedIn, tenant: str) -> None:
    number = await new_case(analyst.client, tenant)
    evidence_id = await upload(analyst.client, tenant, number, b"fresh")
    response = await analyst.client.get(url(tenant, number, f"/{evidence_id}/download"))
    assert response.content == b"fresh"
    custody = await report(analyst.client, tenant, number, evidence_id)
    assert actions(custody) == ["collected", "uploaded", "accessed"]


SAMPLE = b"MZ\x90\x00 not really malware"


async def upload_sample(client: AsyncClient, tenant: str, number: int, data: bytes = SAMPLE):
    return await client.post(
        url(tenant, number, "/malware"),
        data=COLLECTION | {"description": "Dropper from the phish"},
        files={"file": ("invoice.exe", data, "application/octet-stream")},
    )


async def test_malware_is_quarantined_and_gated(
    make_user: MakeUser, analyst: SignedIn, tenant: str, owner: AsyncEngine
) -> None:
    number = await new_case(analyst.client, tenant)

    response = await upload_sample(analyst.client, tenant, number)

    assert response.status_code == 201, response.text
    item = response.json()
    assert (item["kind"], item["status"], item["size"]) == ("malware", "hashed", len(SAMPLE))
    assert item["sha256"] == hashlib.sha256(SAMPLE).hexdigest()
    download_url = url(tenant, number, f"/{item['id']}/download")
    assert (await analyst.client.get(download_url)).status_code == 403
    assert (await audit_actions(owner, f"user:{analyst.id}"))[-1] == "access.denied"

    handler = await make_user({tenant: ["analyst", "malware:read"]})
    response = await handler.client.get(download_url)
    assert response.status_code == 200
    assert 'filename="invoice.exe.zip"' in response.headers["content-disposition"]
    assert SAMPLE not in response.content  # never stored or served in the clear
    with pyzipper.AESZipFile(io.BytesIO(response.content)) as archive:
        archive.setpassword(b"infected")
        assert archive.read("invoice.exe") == SAMPLE
    custody = await report(handler.client, tenant, number, item["id"])
    assert actions(custody) == ["collected", "uploaded", "hashed", "accessed", "verified"]


async def test_malware_size_is_capped(
    analyst: SignedIn, tenant: str, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "malware_max_mb", 1)
    number = await new_case(analyst.client, tenant)
    response = await upload_sample(analyst.client, tenant, number, b"x" * (storage.MB + 1))
    assert response.status_code == 413


async def test_malware_needs_collection_details(analyst: SignedIn, tenant: str) -> None:
    number = await new_case(analyst.client, tenant)
    response = await analyst.client.post(
        url(tenant, number, "/malware"),
        data={"collected_by": "J Doe", "collected_at": "yesterday"},
        files={"file": ("a.exe", b"x")},
    )
    assert response.status_code == 422


async def test_references_and_transfers(analyst: SignedIn, tenant: str) -> None:
    number = await new_case(analyst.client, tenant)
    created = await analyst.client.post(
        url(tenant, number, "/references"),
        json={
            "name": "Laptop image",
            "location": "Evidence locker 3, bag 0042",
            "sha256": "A" * 64,
            "size": 512_000_000_000,
            "collection": COLLECTION,
        },
    )
    assert created.status_code == 201, created.text
    item = created.json()
    assert (item["status"], item["sha256"]) == ("reference", "a" * 64)
    download = await analyst.client.get(url(tenant, number, f"/{item['id']}/download"))
    assert download.status_code == 404

    transferred = await analyst.client.post(
        url(tenant, number, f"/{item['id']}/transfer"),
        json={"to": "Outside counsel", "reason": "Litigation review", "location": "Courier"},
    )
    assert transferred.status_code == 200, transferred.text
    custody = transferred.json()
    assert actions(custody) == ["collected", "registered", "transferred"]
    assert custody["entries"][-1]["detail"] == {
        "to": "Outside counsel",
        "reason": "Litigation review",
        "location": "Courier",
    }
    assert custody["chain_intact"] is True


@pytest.mark.parametrize(
    "statement",
    ["UPDATE custody_log SET actor = 'x'", "DELETE FROM custody_log", "TRUNCATE custody_log"],
)
async def test_custody_log_is_append_only(owner: AsyncEngine, statement: str) -> None:
    async with SessionLocal() as db:
        with pytest.raises(DBAPIError, match="permission denied"):
            await db.execute(text(statement))
    with pytest.raises(DBAPIError, match="append-only"):
        async with owner.begin() as conn:
            await conn.execute(text(statement))


async def test_tampered_custody_chain_is_reported(
    analyst: SignedIn, tenant: str, owner: AsyncEngine
) -> None:
    number = await new_case(analyst.client, tenant)
    evidence_id = await upload(analyst.client, tenant, number, b"log")

    async def set_actor(actor: str) -> None:
        async with owner.begin() as conn:
            await conn.execute(
                text("ALTER TABLE custody_log DISABLE TRIGGER custody_log_append_only")
            )
            await conn.execute(
                text(
                    "UPDATE custody_log SET actor = :a "
                    "WHERE evidence_id = :e AND action = 'uploaded'"
                ),
                {"a": actor, "e": evidence_id},
            )
            await conn.execute(
                text("ALTER TABLE custody_log ENABLE TRIGGER custody_log_append_only")
            )

    original = (await report(analyst.client, tenant, number, evidence_id))["entries"][1]["actor"]
    await set_actor("user:someone-else")
    try:
        assert (await report(analyst.client, tenant, number, evidence_id))["chain_intact"] is False
    finally:
        await set_actor(original)
    assert (await report(analyst.client, tenant, number, evidence_id))["chain_intact"] is True


async def test_legal_hold_blocks_deletion(
    make_user: MakeUser, analyst: SignedIn, tenant: str
) -> None:
    counsel = await make_user({tenant: ["legal"]})
    number = await new_case(analyst.client, tenant)
    evidence_id = await upload(analyst.client, tenant, number, b"keep me")
    case_url = f"/api/tenants/{tenant}/cases/{number}"
    delete_url = url(tenant, number, f"/{evidence_id}")

    hold = {"on": True, "reason": "Litigation expected"}
    assert (await analyst.client.put(f"{case_url}/legal-hold", json=hold)).status_code == 403
    held = await counsel.client.put(f"{case_url}/legal-hold", json=hold)
    assert held.json()["legal_hold"] is True

    blocked = await analyst.client.delete(delete_url, params={"reason": "duplicate"})
    assert blocked.status_code == 422
    assert "legal hold" in blocked.text

    await counsel.client.put(f"{case_url}/legal-hold", json={"on": False, "reason": "Settled"})
    assert (
        await analyst.client.delete(delete_url, params={"reason": "duplicate"})
    ).status_code == 204
    custody = await report(analyst.client, tenant, number, evidence_id)
    assert custody["evidence"]["status"] == "deleted"
    assert custody["entries"][-1]["detail"] == {"reason": "duplicate"}
    again = await analyst.client.delete(delete_url, params={"reason": "again"})
    assert "already deleted" in again.text
    transfer = await analyst.client.post(
        url(tenant, number, f"/{evidence_id}/transfer"), json={"to": "x", "reason": "y"}
    )
    assert "cannot change hands" in transfer.text
    assert (
        await analyst.client.get(url(tenant, number, f"/{evidence_id}/download"))
    ).status_code == 404


async def test_evidence_follows_case_visibility(make_user: MakeUser, tenant: str) -> None:
    insider = await make_user({tenant: ["analyst", "restricted_case"]})
    analyst = await make_user({tenant: ["analyst"]})
    number = await new_case(insider.client, tenant)
    evidence_id = await upload(insider.client, tenant, number, b"secret")
    await insider.client.patch(f"/api/tenants/{tenant}/cases/{number}", json={"restricted": True})

    for path in (f"/{evidence_id}/download", f"/{evidence_id}/custody"):
        assert (await analyst.client.get(url(tenant, number, path))).status_code == 404
    other = await new_case(analyst.client, tenant)
    assert (
        await analyst.client.get(url(tenant, other, f"/{evidence_id}/custody"))
    ).status_code == 404


async def test_merge_moves_evidence(analyst: SignedIn, tenant: str) -> None:
    source = await new_case(analyst.client, tenant)
    target = await new_case(analyst.client, tenant)
    evidence_id = await upload(analyst.client, tenant, source, b"pcap")
    merged = await analyst.client.post(
        f"/api/tenants/{tenant}/cases/{source}/merge", json={"into": target}
    )
    assert [e["id"] for e in merged.json()["evidence"]] == [evidence_id]


def test_content_disposition_is_always_an_attachment() -> None:
    assert content_disposition("report.pdf") == (
        "attachment; filename=\"report.pdf\"; filename*=UTF-8''report.pdf"
    )
    assert content_disposition('évidence "1".html') == (
        "attachment; filename=\"_vidence__1_.html\"; filename*=UTF-8''%C3%A9vidence%20%221%22.html"
    )


async def test_create_bucket_job_is_idempotent() -> None:
    assert await evidence_hash.create_bucket() == 0
