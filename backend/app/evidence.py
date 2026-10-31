"""Evidence: uploads, references, malware samples, integrity checks, and deletion."""

import asyncio
import hashlib
import io
import math
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

import pyzipper
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import storage
from app.custody import CustodyAction, record
from app.models import Case, Evidence
from app.tenant_config import MAX_TEXT

Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
FileName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
LongText = Annotated[str, StringConstraints(max_length=MAX_TEXT)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-fA-F]{64}$")]
MALWARE_PASSWORD = b"infected"
TIB = 1024**4


class EvidenceKind(StrEnum):
    FILE = "file"
    MALWARE = "malware"
    REFERENCE = "reference"


class EvidenceStatus(StrEnum):
    UPLOADING = "uploading"
    STORED = "stored"  # awaiting its hash
    HASHED = "hashed"
    MISMATCH = "mismatch"  # a download did not match the stored hash
    REFERENCE = "reference"
    DELETED = "deleted"


class Collection(BaseModel):
    """Who collected the evidence, when, where, and how."""

    collected_by: Short
    collected_at: AwareDatetime
    collected_from: Short | None = None
    method: Short | None = None


class UploadIn(BaseModel):
    name: FileName
    description: LongText = ""
    size: int = Field(gt=0, le=5 * TIB)
    collection: Collection


class ReferenceIn(BaseModel):
    name: FileName
    description: LongText = ""
    location: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]
    sha256: Sha256 | None = None
    size: int | None = Field(default=None, ge=0)
    collection: Collection


class TransferIn(BaseModel):
    to: Short
    reason: Short
    location: Short | None = None


class EvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: EvidenceKind
    name: str
    description: str
    size: int | None
    location: str | None
    status: EvidenceStatus
    sha256: str | None
    created_by: str
    created_at: datetime


class UploadStart(BaseModel):
    evidence: EvidenceOut
    part_size: int
    part_urls: list[str]


def content_disposition(name: str) -> str:
    """Always an attachment, so a browser never renders evidence."""
    fallback = re.sub(r"[^A-Za-z0-9._-]", "_", name) or "evidence"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name)}"


def _new(case: Case, actor: str, kind: EvidenceKind, name: str, description: str) -> Evidence:
    return Evidence(
        tenant_id=case.tenant_id,
        case_id=case.id,
        kind=kind,
        name=name,
        description=description,
        status=EvidenceStatus.UPLOADING,
        created_by=actor,
    )


async def _collected(db: AsyncSession, evidence: Evidence, actor: str, c: Collection) -> None:
    await record(db, evidence, actor, CustodyAction.COLLECTED, **c.model_dump(mode="json"))


async def start_upload(db: AsyncSession, case: Case, actor: str, body: UploadIn) -> UploadStart:
    evidence = _new(case, actor, EvidenceKind.FILE, body.name, body.description)
    evidence.size = body.size
    db.add(evidence)
    await db.flush()
    key = evidence.object_key = f"{case.tenant_id}/{evidence.id}"
    upload_id = evidence.upload_id = await asyncio.to_thread(storage.start_upload, key)
    await _collected(db, evidence, actor, body.collection)
    size = storage.part_size(body.size)
    urls = storage.part_urls(key, upload_id, math.ceil(body.size / size))
    return UploadStart(
        evidence=EvidenceOut.model_validate(evidence), part_size=size, part_urls=urls
    )


async def finish_upload(db: AsyncSession, evidence: Evidence, actor: str) -> None:
    assert evidence.object_key and evidence.upload_id  # noqa: S101 - checked by the caller
    evidence.size = await asyncio.to_thread(
        storage.finish_upload, evidence.object_key, evidence.upload_id
    )
    evidence.upload_id = None
    evidence.status = EvidenceStatus.STORED
    await record(db, evidence, actor, CustodyAction.UPLOADED, size=evidence.size)


async def abort_upload(db: AsyncSession, evidence: Evidence, actor: str) -> None:
    assert evidence.object_key and evidence.upload_id  # noqa: S101 - checked by the caller
    await asyncio.to_thread(storage.abort_upload, evidence.object_key, evidence.upload_id)
    evidence.upload_id = None
    evidence.status = EvidenceStatus.DELETED
    evidence.deleted_at = datetime.now(UTC)
    await record(db, evidence, actor, CustodyAction.DELETED, reason="upload abandoned")


async def add_reference(db: AsyncSession, case: Case, actor: str, body: ReferenceIn) -> Evidence:
    evidence = _new(case, actor, EvidenceKind.REFERENCE, body.name, body.description)
    evidence.status = EvidenceStatus.REFERENCE
    evidence.location = body.location
    evidence.size = body.size
    evidence.sha256 = body.sha256 and body.sha256.lower()
    db.add(evidence)
    await db.flush()
    await _collected(db, evidence, actor, body.collection)
    await record(
        db,
        evidence,
        actor,
        CustodyAction.REGISTERED,
        location=body.location,
        sha256=evidence.sha256,
    )
    return evidence


def wrap_sample(name: str, data: bytes) -> bytes:
    """Store a malware sample only inside an AES-encrypted zip, so it can never run as is."""
    buffer = io.BytesIO()
    with pyzipper.AESZipFile(
        buffer, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
    ) as archive:
        archive.setpassword(MALWARE_PASSWORD)
        archive.writestr(name, data)
    return buffer.getvalue()


async def add_malware(
    db: AsyncSession,
    case: Case,
    actor: str,
    name: str,
    description: str,
    data: bytes,
    collection: Collection,
) -> Evidence:
    evidence = _new(case, actor, EvidenceKind.MALWARE, name, description)
    db.add(evidence)
    await db.flush()
    wrapped = await asyncio.to_thread(wrap_sample, name, data)
    key = evidence.object_key = f"{case.tenant_id}/{evidence.id}"
    await asyncio.to_thread(storage.put, key, wrapped)
    evidence.size = len(data)
    evidence.sha256 = hashlib.sha256(data).hexdigest()
    evidence.object_sha256 = hashlib.sha256(wrapped).hexdigest()
    evidence.status = EvidenceStatus.HASHED
    await _collected(db, evidence, actor, collection)
    await record(db, evidence, actor, CustodyAction.UPLOADED, size=evidence.size, quarantined=True)
    await record(db, evidence, actor, CustodyAction.HASHED, sha256=evidence.sha256)
    return evidence


def _sha256_of(key: str) -> str:
    digest = hashlib.sha256()
    for chunk in storage.stream(key):
        digest.update(chunk)
    return digest.hexdigest()


async def hash_stored(db: AsyncSession, evidence: Evidence, actor: str) -> None:
    """Hash a newly stored object by streaming it from storage."""
    assert evidence.object_key  # noqa: S101 - stored evidence always has an object
    evidence.sha256 = evidence.object_sha256 = await asyncio.to_thread(
        _sha256_of, evidence.object_key
    )
    evidence.status = EvidenceStatus.HASHED
    await record(db, evidence, actor, CustodyAction.HASHED, sha256=evidence.sha256)


async def delete(db: AsyncSession, evidence: Evidence, actor: str, reason: str) -> None:
    if evidence.object_key and evidence.status != EvidenceStatus.DELETED:
        await asyncio.to_thread(storage.delete, evidence.object_key)
    evidence.status = EvidenceStatus.DELETED
    evidence.deleted_at = datetime.now(UTC)
    await record(db, evidence, actor, CustodyAction.DELETED, reason=reason)


async def for_case(db: AsyncSession, case: Case) -> list[EvidenceOut]:
    query = select(Evidence).where(Evidence.case_id == case.id).order_by(Evidence.created_at)
    return [EvidenceOut.model_validate(e) for e in await db.scalars(query)]


async def move(db: AsyncSession, source: Case, target: Case) -> list[str]:
    moved = await db.scalars(
        update(Evidence)
        .where(Evidence.case_id == source.id)
        .values(case_id=target.id)
        .returning(Evidence.id)
    )
    return sorted(map(str, moved))
