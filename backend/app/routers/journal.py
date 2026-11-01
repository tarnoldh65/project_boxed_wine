from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app import cases, journal, tenant_config
from app.auth import TenantContext, require
from app.cases import CaseOut
from app.db import Db
from app.journal import CommentIn, NoteIn, RevisionOut, TimelineIn
from app.models import Case, Note

router = APIRouter(prefix="/api/tenants/{slug}/cases/{number}", tags=["journal"])

Reader = Annotated[TenantContext, require("cases:read")]
Writer = Annotated[TenantContext, require("cases:write")]


async def _out(db: Db, ctx: TenantContext, case: Case) -> CaseOut:
    return await cases.case_out(db, ctx, case, await tenant_config.load(db, ctx.tenant.id))


async def _note(db: Db, case: Case, note_id: UUID) -> Note:
    note = await db.scalar(select(Note).where(Note.id == note_id, Note.case_id == case.id))
    if note is None:
        raise HTTPException(404, "Note not found")
    return note


@router.post("/timeline", status_code=201)
async def add_event(number: int, body: TimelineIn, ctx: Writer, db: Db) -> CaseOut:
    case = await cases.get_case(db, ctx, number)
    if problem := await journal.link_problem(db, case, body):
        cases.invalid(problem)
    event = await journal.add_event(db, case, ctx.principal.actor, body)
    await ctx.audit(
        db, "timeline.create", f"case:{number}", event=str(event.id), milestone=body.milestone
    )
    return await _out(db, ctx, case)


@router.delete("/timeline/{event_id}", status_code=204)
async def delete_event(number: int, event_id: UUID, ctx: Writer, db: Db) -> None:
    case = await cases.get_case(db, ctx, number)
    if not await journal.delete_event(db, case, event_id):
        raise HTTPException(404, "Timeline event not found")
    await ctx.audit(db, "timeline.delete", f"case:{number}", event=str(event_id))


@router.post("/notes", status_code=201)
async def add_note(number: int, body: NoteIn, ctx: Writer, db: Db) -> CaseOut:
    case = await cases.get_case(db, ctx, number)
    note, notified = await journal.add_note(db, case, ctx.principal.actor, body)
    await ctx.audit(db, "note.create", f"case:{number}", note=str(note.id), mentioned=notified)
    return await _out(db, ctx, case)


@router.patch("/notes/{note_id}")
async def edit_note(number: int, note_id: UUID, body: NoteIn, ctx: Writer, db: Db) -> CaseOut:
    case = await cases.get_case(db, ctx, number)
    note = await _note(db, case, note_id)
    notified = await journal.edit_note(db, case, note, ctx.principal.actor, body)
    if notified is not None:
        await ctx.audit(
            db, "note.update", f"case:{number}", note=str(note.id), revision=note.revision,
            mentioned=notified,
        )  # fmt: skip
    return await _out(db, ctx, case)


@router.get("/notes/{note_id}/revisions")
async def note_revisions(number: int, note_id: UUID, ctx: Reader, db: Db) -> list[RevisionOut]:
    case = await cases.get_case(db, ctx, number)
    note = await _note(db, case, note_id)
    await ctx.audit(db, "note.history", f"case:{number}", note=str(note.id))
    return await journal.revisions(db, note)


@router.post("/comments", status_code=201)
async def add_comment(number: int, body: CommentIn, ctx: Writer, db: Db) -> CaseOut:
    case = await cases.get_case(db, ctx, number)
    comment = await journal.add_comment(db, case, ctx.principal.actor, body)
    await ctx.audit(db, "comment.create", f"case:{number}", comment=str(comment.id))
    return await _out(db, ctx, case)
