"""Case journal: the incident timeline, investigation notes with revisions, and comments."""

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Case,
    CaseAsset,
    CaseObservable,
    Comment,
    Evidence,
    Membership,
    Note,
    NoteRevision,
    Notification,
    TimelineEvent,
    User,
)
from app.tenant_config import MAX_TEXT, UtcDatetime

Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TEXT)]
NoteBody = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100_000)
]
CommentBody = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)
]
MENTION = re.compile(r"(?<![\w@])@([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")


class Milestone(StrEnum):
    """Key incident moments, used for dwell time and other metrics."""

    FIRST_COMPROMISE = "first_compromise"
    DETECTION = "detection"
    CONTAINMENT = "containment"
    ERADICATION = "eradication"


class TimelineIn(BaseModel):
    occurred_at: UtcDatetime
    source: Short
    description: Text
    milestone: Milestone | None = None
    observable_id: UUID | None = None
    asset_id: UUID | None = None
    evidence_id: UUID | None = None


class TimelineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    occurred_at: datetime
    source: str
    description: str
    milestone: Milestone | None
    observable_id: UUID | None
    asset_id: UUID | None
    evidence_id: UUID | None
    recorded_by: str
    recorded_at: datetime


class NoteIn(BaseModel):
    body: NoteBody
    privileged: bool = False


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    body: str
    privileged: bool
    revision: int
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime


class RevisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    revision: int
    body: str
    privileged: bool
    edited_by: str
    edited_at: datetime


class CommentIn(BaseModel):
    body: CommentBody


class CommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    body: str
    author: str
    created_at: datetime


# --- Timeline -----------------------------------------------------------------------------


async def link_problem(db: AsyncSession, case: Case, body: TimelineIn) -> str | None:
    """Why the event cannot be recorded as given, or None."""
    if body.milestone is not None and await db.scalar(
        select(TimelineEvent.id).where(
            TimelineEvent.case_id == case.id, TimelineEvent.milestone == body.milestone
        )
    ):
        return f"the case already has a {body.milestone.replace('_', ' ')} milestone"
    checks = [
        (body.observable_id, select(CaseObservable.observable_id).where(
            CaseObservable.case_id == case.id, CaseObservable.observable_id == body.observable_id
        ), "observable"),
        (body.asset_id, select(CaseAsset.asset_id).where(
            CaseAsset.case_id == case.id, CaseAsset.asset_id == body.asset_id
        ), "asset"),
        (body.evidence_id, select(Evidence.id).where(
            Evidence.case_id == case.id, Evidence.id == body.evidence_id
        ), "evidence"),
    ]  # fmt: skip
    for value, query, what in checks:
        if value is not None and await db.scalar(query) is None:
            return f"the {what} must belong to this case"
    return None


async def add_event(db: AsyncSession, case: Case, actor: str, body: TimelineIn) -> TimelineEvent:
    event = TimelineEvent(
        tenant_id=case.tenant_id, case_id=case.id, recorded_by=actor, **body.model_dump()
    )
    db.add(event)
    await db.flush()
    return event


async def timeline(db: AsyncSession, case: Case) -> list[TimelineOut]:
    events = await db.scalars(
        select(TimelineEvent)
        .where(TimelineEvent.case_id == case.id)
        .order_by(TimelineEvent.occurred_at, TimelineEvent.recorded_at)
    )
    return [TimelineOut.model_validate(e) for e in events]


async def delete_event(db: AsyncSession, case: Case, event_id: UUID) -> bool:
    removed = await db.scalar(
        delete(TimelineEvent)
        .where(TimelineEvent.case_id == case.id, TimelineEvent.id == event_id)
        .returning(TimelineEvent.id)
    )
    return removed is not None


# --- Notes --------------------------------------------------------------------------------


def mentions(body: str) -> set[str]:
    return set(MENTION.findall(body))


async def notify_mentions(
    db: AsyncSession, case: Case, note: Note, usernames: set[str]
) -> list[str]:
    """Queue an email to each mentioned user who can see the case, once per note."""
    if not usernames:
        return []
    rows = await db.execute(
        select(User, Membership.grant_name)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.tenant_id == case.tenant_id, User.username.in_(usernames))
    )
    grants: dict[UUID, set[str]] = {}
    users: dict[UUID, User] = {}
    for user, grant in rows:
        users[user.id] = user
        grants.setdefault(user.id, set()).add(grant)
    notified = []
    for user in users.values():
        if case.restricted and "restricted_case" not in grants[user.id]:
            continue
        if not user.email:
            continue
        await db.execute(
            insert(Notification)
            .values(
                tenant_id=case.tenant_id,
                case_id=case.id,
                rule="mention",
                trigger="mention",
                step=f"note:{note.id}:{user.id}",
                emails=[user.email],
            )
            .on_conflict_do_nothing(index_elements=["case_id", "rule", "trigger", "step"])
        )
        notified.append(user.username)
    return sorted(notified)


async def _revise(db: AsyncSession, note: Note, actor: str) -> None:
    db.add(
        NoteRevision(
            tenant_id=note.tenant_id,
            note_id=note.id,
            revision=note.revision,
            body=note.body,
            privileged=note.privileged,
            edited_by=actor,
        )
    )
    await db.flush()


async def add_note(
    db: AsyncSession, case: Case, actor: str, body: NoteIn
) -> tuple[Note, list[str]]:
    note = Note(
        tenant_id=case.tenant_id,
        case_id=case.id,
        body=body.body,
        privileged=body.privileged,
        revision=1,
        created_by=actor,
        updated_by=actor,
    )
    db.add(note)
    await db.flush()
    await _revise(db, note, actor)
    return note, await notify_mentions(db, case, note, mentions(note.body))


async def edit_note(
    db: AsyncSession, case: Case, note: Note, actor: str, body: NoteIn
) -> list[str] | None:
    """Save a new revision; returns who was newly mentioned, or None if nothing changed."""
    if (note.body, note.privileged) == (body.body, body.privileged):
        return None
    added = mentions(body.body) - mentions(note.body)
    note.body, note.privileged = body.body, body.privileged
    note.revision += 1
    note.updated_by = actor
    note.updated_at = datetime.now(UTC)
    await _revise(db, note, actor)
    return await notify_mentions(db, case, note, added)


async def notes(db: AsyncSession, case: Case) -> list[NoteOut]:
    found = await db.scalars(select(Note).where(Note.case_id == case.id).order_by(Note.created_at))
    return [NoteOut.model_validate(n) for n in found]


async def revisions(db: AsyncSession, note: Note) -> list[RevisionOut]:
    found = await db.scalars(
        select(NoteRevision).where(NoteRevision.note_id == note.id).order_by(NoteRevision.revision)
    )
    return [RevisionOut.model_validate(r) for r in found]


# --- Comments -----------------------------------------------------------------------------


async def add_comment(db: AsyncSession, case: Case, actor: str, body: CommentIn) -> Comment:
    comment = Comment(tenant_id=case.tenant_id, case_id=case.id, body=body.body, author=actor)
    db.add(comment)
    await db.flush()
    return comment


async def comments(db: AsyncSession, case: Case) -> list[CommentOut]:
    found = await db.scalars(
        select(Comment).where(Comment.case_id == case.id).order_by(Comment.created_at)
    )
    return [CommentOut.model_validate(c) for c in found]


async def move(db: AsyncSession, source: Case, target: Case) -> dict[str, Sequence[str]]:
    """Move the journal to the case it is merged into; the target keeps its own milestones."""
    taken = select(TimelineEvent.milestone).where(
        TimelineEvent.case_id == target.id, TimelineEvent.milestone.is_not(None)
    )
    await db.execute(
        update(TimelineEvent)
        .where(TimelineEvent.case_id == source.id, TimelineEvent.milestone.in_(taken))
        .values(milestone=None)
    )
    moved: dict[str, Sequence[str]] = {}
    for name, model in (("timeline", TimelineEvent), ("notes", Note), ("comments", Comment)):
        ids = await db.scalars(
            update(model)
            .where(model.case_id == source.id)
            .values(case_id=target.id)
            .returning(model.id)
        )
        moved[name] = sorted(map(str, ids))
    return moved
