import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from botocore.exceptions import ClientError
from fastapi import APIRouter, Form, HTTPException, Query, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from starlette.background import BackgroundTask

from app import audit, cases, custody, evidence, storage
from app.auth import TenantContext, deny, require
from app.config import settings
from app.custody import CustodyAction, CustodyEntryOut
from app.db import Db, SessionLocal, set_tenant
from app.evidence import (
    Collection,
    EvidenceKind,
    EvidenceOut,
    EvidenceStatus,
    ReferenceIn,
    TransferIn,
    UploadIn,
    UploadStart,
)
from app.models import Case, Evidence

router = APIRouter(prefix="/api/tenants/{slug}/cases/{number}/evidence", tags=["evidence"])

Reader = Annotated[TenantContext, require("cases:read")]
Writer = Annotated[TenantContext, require("cases:write")]
DOWNLOADABLE = {EvidenceStatus.STORED, EvidenceStatus.HASHED, EvidenceStatus.MISMATCH}


class CustodyReport(BaseModel):
    evidence: EvidenceOut
    entries: list[CustodyEntryOut]
    chain_intact: bool
    generated_at: datetime


async def _find(
    db: Db, ctx: TenantContext, number: int, evidence_id: UUID
) -> tuple[Case, Evidence]:
    case = await cases.get_case(db, ctx, number)
    found = await db.scalar(
        select(Evidence).where(Evidence.id == evidence_id, Evidence.case_id == case.id)
    )
    if found is None:
        raise HTTPException(404, "Evidence not found")
    return case, found


@router.post("/uploads", status_code=201)
async def start_upload(number: int, body: UploadIn, ctx: Writer, db: Db) -> UploadStart:
    case = await cases.get_case(db, ctx, number)
    started = await evidence.start_upload(db, case, ctx.principal.actor, body)
    await ctx.audit(
        db, "evidence.upload_start", f"case:{number}", evidence=str(started.evidence.id)
    )
    return started


@router.post("/{evidence_id}/complete")
async def complete_upload(number: int, evidence_id: UUID, ctx: Writer, db: Db) -> EvidenceOut:
    _, item = await _find(db, ctx, number, evidence_id)
    if item.status != EvidenceStatus.UPLOADING:
        cases.invalid("the upload is not in progress")
    try:
        await evidence.finish_upload(db, item, ctx.principal.actor)
    except ClientError, ValueError:
        cases.invalid("the upload is incomplete; upload every part and try again")
    await ctx.audit(db, "evidence.upload", f"case:{number}", evidence=str(item.id), size=item.size)
    return EvidenceOut.model_validate(item)


@router.post("/{evidence_id}/abort", status_code=204)
async def abort_upload(number: int, evidence_id: UUID, ctx: Writer, db: Db) -> None:
    _, item = await _find(db, ctx, number, evidence_id)
    if item.status != EvidenceStatus.UPLOADING:
        cases.invalid("the upload is not in progress")
    await evidence.abort_upload(db, item, ctx.principal.actor)
    await ctx.audit(db, "evidence.upload_abort", f"case:{number}", evidence=str(item.id))


@router.post("/references", status_code=201)
async def add_reference(number: int, body: ReferenceIn, ctx: Writer, db: Db) -> EvidenceOut:
    case = await cases.get_case(db, ctx, number)
    item = await evidence.add_reference(db, case, ctx.principal.actor, body)
    await ctx.audit(db, "evidence.reference", f"case:{number}", evidence=str(item.id))
    return EvidenceOut.model_validate(item)


@router.post("/malware", status_code=201)
async def add_malware(
    number: int,
    file: UploadFile,
    collected_by: Annotated[str, Form()],
    collected_at: Annotated[str, Form()],
    ctx: Writer,
    db: Db,
    description: Annotated[str, Form()] = "",
    collected_from: Annotated[str | None, Form()] = None,
    method: Annotated[str | None, Form()] = None,
) -> EvidenceOut:
    """Upload a malware sample; it is stored only inside a password-protected AES zip."""
    case = await cases.get_case(db, ctx, number)
    try:
        collection = Collection.model_validate(
            {
                "collected_by": collected_by,
                "collected_at": collected_at,
                "collected_from": collected_from,
                "method": method,
            }
        )
    except ValidationError as e:
        raise RequestValidationError(e.errors(include_url=False, include_context=False)) from e
    limit = settings.malware_max_mb * storage.MB
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"Samples are limited to {settings.malware_max_mb} MB")
    item = await evidence.add_malware(
        db, case, ctx.principal.actor, file.filename or "sample", description, data, collection
    )
    await ctx.audit(db, "evidence.malware", f"case:{number}", evidence=str(item.id))
    return EvidenceOut.model_validate(item)


async def _record_verification(tenant_id: UUID, evidence_id: UUID, actor: str, sha256: str) -> None:
    async with SessionLocal() as db:
        await set_tenant(db, tenant_id)
        item = await db.get_one(Evidence, evidence_id)
        if sha256 == item.object_sha256:
            await custody.record(db, item, actor, CustodyAction.VERIFIED, sha256=sha256)
        else:
            item.status = EvidenceStatus.MISMATCH
            await custody.record(
                db, item, actor, CustodyAction.MISMATCH, expected=item.object_sha256, actual=sha256
            )
            await audit.record(
                db, actor, "evidence.mismatch", f"evidence:{item.id}", tenant_id,
                {"expected": item.object_sha256, "actual": sha256},
            )  # fmt: skip
        await db.commit()


@router.get("/{evidence_id}/download")
async def download(number: int, evidence_id: UUID, ctx: Reader, db: Db) -> StreamingResponse:
    _, item = await _find(db, ctx, number, evidence_id)
    if item.kind == EvidenceKind.MALWARE and "malware:read" not in ctx.permissions:
        await deny(db, ctx.principal, f"evidence:{item.id}:malware", ctx.tenant.id)
    if item.status not in DOWNLOADABLE or item.object_key is None:
        raise HTTPException(404, "Evidence has no stored content")
    await custody.record(db, item, ctx.principal.actor, CustodyAction.ACCESSED)
    await ctx.audit(db, "evidence.download", f"case:{number}", evidence=str(item.id))

    digest = hashlib.sha256()
    finished = False
    key = item.object_key

    def body() -> Iterator[bytes]:
        nonlocal finished
        for chunk in storage.stream(key):
            digest.update(chunk)
            yield chunk
        finished = True

    async def verify() -> None:
        # Only a complete read can be compared; an unhashed object has nothing to compare to.
        if finished and item.object_sha256 is not None:
            await _record_verification(
                ctx.tenant.id, item.id, ctx.principal.actor, digest.hexdigest()
            )

    name = f"{item.name}.zip" if item.kind == EvidenceKind.MALWARE else item.name
    return StreamingResponse(
        body(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": evidence.content_disposition(name)},
        background=BackgroundTask(verify),
    )


async def _report(db: Db, item: Evidence) -> CustodyReport:
    log = await custody.entries(db, item)
    return CustodyReport(
        evidence=EvidenceOut.model_validate(item),
        entries=[CustodyEntryOut.model_validate(e) for e in log],
        chain_intact=custody.chain_intact(log),
        generated_at=datetime.now(UTC),
    )


@router.get("/{evidence_id}/custody")
async def custody_report(number: int, evidence_id: UUID, ctx: Reader, db: Db) -> CustodyReport:
    _, item = await _find(db, ctx, number, evidence_id)
    await ctx.audit(db, "evidence.custody_report", f"case:{number}", evidence=str(item.id))
    return await _report(db, item)


@router.post("/{evidence_id}/transfer")
async def transfer(
    number: int, evidence_id: UUID, body: TransferIn, ctx: Writer, db: Db
) -> CustodyReport:
    _, item = await _find(db, ctx, number, evidence_id)
    if item.status == EvidenceStatus.DELETED:
        cases.invalid("deleted evidence cannot change hands")
    await custody.record(
        db, item, ctx.principal.actor, CustodyAction.TRANSFERRED, **body.model_dump(mode="json")
    )
    await ctx.audit(db, "evidence.transfer", f"case:{number}", evidence=str(item.id))
    return await _report(db, item)


@router.delete("/{evidence_id}", status_code=204)
async def delete(
    number: int,
    evidence_id: UUID,
    ctx: Writer,
    db: Db,
    reason: Annotated[str, Query(min_length=1, max_length=500)],
) -> None:
    case, item = await _find(db, ctx, number, evidence_id)
    if case.legal_hold:
        cases.invalid("the case is under legal hold; evidence cannot be deleted")
    if item.status == EvidenceStatus.DELETED:
        cases.invalid("the evidence is already deleted")
    await evidence.delete(db, item, ctx.principal.actor, reason)
    await ctx.audit(db, "evidence.delete", f"case:{number}", evidence=str(item.id), reason=reason)
