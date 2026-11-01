"""Post-incident review: what happened and why, and the actions agreed to prevent a repeat."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Case, Pir, PirAction
from app.tenant_config import MAX_TEXT, UtcDatetime

Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=MAX_TEXT)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
FIELDS = ("root_cause", "contributing_factors", "went_well", "went_poorly")


class PirIn(BaseModel):
    root_cause: Text = ""
    contributing_factors: Text = ""
    went_well: Text = ""
    went_poorly: Text = ""


class ActionIn(BaseModel):
    title: Title
    owner_id: UUID | None = None
    due_at: UtcDatetime | None = None


class ActionUpdate(BaseModel):
    done: bool


class ActionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    owner_id: UUID | None
    due_at: datetime | None
    done_at: datetime | None
    created_by: str
    created_at: datetime


class PirOut(PirIn):
    complete: bool
    updated_by: str | None
    updated_at: datetime | None
    actions: list[ActionOut]


def complete(pir: Pir | None) -> bool:
    return pir is not None and all(getattr(pir, field) for field in FIELDS)


async def get(db: AsyncSession, case: Case) -> Pir | None:
    return await db.get(Pir, case.id)


async def save(db: AsyncSession, case: Case, actor: str, body: PirIn) -> None:
    values = body.model_dump() | {"updated_by": actor, "updated_at": datetime.now(UTC)}
    await db.execute(
        insert(Pir)
        .values(case_id=case.id, tenant_id=case.tenant_id, **values)
        .on_conflict_do_update(index_elements=["case_id"], set_=values)
    )


async def add_action(db: AsyncSession, case: Case, actor: str, body: ActionIn) -> PirAction:
    action = PirAction(
        tenant_id=case.tenant_id, case_id=case.id, created_by=actor, **body.model_dump()
    )
    db.add(action)
    await db.flush()
    return action


async def get_action(db: AsyncSession, case: Case, action_id: UUID) -> PirAction | None:
    return await db.scalar(
        select(PirAction).where(PirAction.case_id == case.id, PirAction.id == action_id)
    )


async def for_case(db: AsyncSession, case: Case) -> PirOut:
    pir = await get(db, case)
    actions = await db.scalars(
        select(PirAction).where(PirAction.case_id == case.id).order_by(PirAction.created_at)
    )
    fields = PirIn() if pir is None else PirIn.model_validate(pir, from_attributes=True)
    return PirOut(
        **fields.model_dump(),
        complete=complete(pir),
        updated_by=pir and pir.updated_by,
        updated_at=pir and pir.updated_at,
        actions=[ActionOut.model_validate(a) for a in actions],
    )
