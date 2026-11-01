"""Breach-notification clocks, materiality decisions, and the log of notifications sent."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated
from uuid import UUID

import holidays
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import Select, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import sla
from app.models import Case, Disclosure, Evidence, MaterialityDecision, RegulatoryClock
from app.tenant_config import MAX_TEXT, ClockTrigger, Regime, TenantConfig, UtcDatetime

Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TEXT)]
US_HOLIDAYS = holidays.country_holidays("US")


class Recipient(StrEnum):
    REGULATOR = "regulator"
    LAW_ENFORCEMENT = "law_enforcement"
    INSURER = "insurer"
    CUSTOMER = "customer"


class ClockOut(BaseModel):
    id: UUID
    regime: str
    name: str
    started_at: datetime
    due_at: datetime
    warn_at: datetime
    met_at: datetime | None
    status: sla.Status


class ActiveClockOut(ClockOut):
    case_number: int


class DecisionIn(BaseModel):
    material: bool
    decided_by: Short
    decided_at: UtcDatetime
    rationale: Text


class DecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    material: bool
    decided_by: str
    decided_at: datetime
    rationale: str
    recorded_by: str
    recorded_at: datetime


class DisclosureIn(BaseModel):
    recipient_type: Recipient
    recipient: Short
    sent_at: UtcDatetime
    summary: Text
    clock_id: UUID | None = None  # the deadline this notification meets
    evidence_ids: list[UUID] = []  # attachments, from the case's evidence


class DisclosureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    recipient_type: Recipient
    recipient: str
    sent_at: datetime
    summary: str
    clock_id: UUID | None
    evidence_ids: list[UUID]
    recorded_by: str
    recorded_at: datetime


# --- Clocks -------------------------------------------------------------------------------


def deadline(regime: Regime, start: datetime) -> datetime:
    """Business days skip weekends and US federal holidays, counted on UTC dates."""
    match regime.unit:
        case "hours":
            return start + timedelta(hours=regime.duration)
        case "days":
            return start + timedelta(days=regime.duration)
        case "business_days":
            day, left = start, regime.duration
            while left:
                day += timedelta(days=1)
                if day.weekday() < 5 and day.date() not in US_HOLIDAYS:
                    left -= 1
            return day


def status(clock: RegulatoryClock, now: datetime) -> sla.Status:
    if clock.met_at is not None:
        return "met" if clock.met_at <= clock.due_at else "late"
    if now > clock.due_at:
        return "breached"
    return "warning" if now >= clock.warn_at else "pending"


def clock_out(clock: RegulatoryClock, now: datetime) -> ClockOut:
    return ClockOut(
        id=clock.id,
        regime=clock.regime,
        name=clock.name,
        started_at=clock.started_at,
        due_at=clock.due_at,
        warn_at=clock.warn_at,
        met_at=clock.met_at,
        status=status(clock, now),
    )


async def start(
    db: AsyncSession, config: TenantConfig, case: Case, trigger: ClockTrigger, at: datetime
) -> list[str]:
    """Start the clock of every regime the case now falls under; returns the regimes started."""
    started = []
    for regime in config.regimes:
        if regime.trigger != trigger or not regime.applies(case.regulated_data, case.data_regions):
            continue
        due = deadline(regime, at)
        key = await db.scalar(
            insert(RegulatoryClock)
            .values(
                tenant_id=case.tenant_id,
                case_id=case.id,
                regime=regime.key,
                name=regime.name,
                started_at=at,
                due_at=due,
                warn_at=max(at, due - timedelta(hours=regime.warn_hours_before)),
            )
            .on_conflict_do_nothing(index_elements=["case_id", "regime"])
            .returning(RegulatoryClock.regime)
        )
        if key is not None:
            started.append(key)
    return started


async def clocks(db: AsyncSession, case: Case, now: datetime) -> list[ClockOut]:
    found = await db.scalars(
        select(RegulatoryClock)
        .where(RegulatoryClock.case_id == case.id)
        .order_by(RegulatoryClock.due_at)
    )
    return [clock_out(c, now) for c in found]


# --- Decisions and notifications ----------------------------------------------------------


async def decide(
    db: AsyncSession, config: TenantConfig, case: Case, actor: str, body: DecisionIn
) -> tuple[MaterialityDecision, list[str]]:
    """Record a decision; a material one starts the clocks that depend on it."""
    decision = MaterialityDecision(
        tenant_id=case.tenant_id, case_id=case.id, recorded_by=actor, **body.model_dump()
    )
    db.add(decision)
    await db.flush()
    started = []
    if body.material:
        started = await start(db, config, case, ClockTrigger.MATERIALITY, body.decided_at)
    return decision, started


async def decisions(db: AsyncSession, case: Case) -> list[DecisionOut]:
    found = await db.scalars(
        select(MaterialityDecision)
        .where(MaterialityDecision.case_id == case.id)
        .order_by(MaterialityDecision.recorded_at)
    )
    return [DecisionOut.model_validate(d) for d in found]


async def disclosure_problem(db: AsyncSession, case: Case, body: DisclosureIn) -> str | None:
    """Why the notification cannot be logged as given, or None."""
    if body.clock_id is not None and not await db.scalar(
        select(RegulatoryClock.id).where(
            RegulatoryClock.case_id == case.id, RegulatoryClock.id == body.clock_id
        )
    ):
        return "the clock must belong to this case"
    own = set(await db.scalars(select(Evidence.id).where(Evidence.case_id == case.id)))
    if not set(body.evidence_ids) <= own:
        return "attachments must be evidence of this case"
    return None


async def disclose(db: AsyncSession, case: Case, actor: str, body: DisclosureIn) -> Disclosure:
    """Log a notification; the first one sent for a clock meets it."""
    disclosure = Disclosure(
        tenant_id=case.tenant_id, case_id=case.id, recorded_by=actor, **body.model_dump()
    )
    db.add(disclosure)
    if body.clock_id is not None:
        await db.execute(
            update(RegulatoryClock)
            .where(RegulatoryClock.id == body.clock_id, RegulatoryClock.met_at.is_(None))
            .values(met_at=body.sent_at)
        )
    await db.flush()
    return disclosure


async def disclosures(db: AsyncSession, case: Case) -> list[DisclosureOut]:
    found = await db.scalars(
        select(Disclosure).where(Disclosure.case_id == case.id).order_by(Disclosure.sent_at)
    )
    return [DisclosureOut.model_validate(d) for d in found]


async def move(db: AsyncSession, source: Case, target: Case) -> dict[str, Sequence[str]]:
    """Move clocks, decisions, and notifications to the case merged into.

    Where both cases run a regime's clock, the target keeps it with the earlier deadline.
    """

    def of(case: Case) -> Select[RegulatoryClock]:
        return select(RegulatoryClock).where(RegulatoryClock.case_id == case.id)

    kept = {c.regime: c for c in await db.scalars(of(target))}
    moved_clocks = []
    for clock in (await db.scalars(of(source))).all():
        other = kept.get(clock.regime)
        if other is None:
            clock.case_id = target.id
            moved_clocks.append(str(clock.id))
        elif clock.due_at < other.due_at:
            other.started_at, other.due_at, other.warn_at = (
                clock.started_at, clock.due_at, clock.warn_at,
            )  # fmt: skip
    moved: dict[str, Sequence[str]] = {"clocks": sorted(moved_clocks)}
    for name, model in (("materiality", MaterialityDecision), ("disclosures", Disclosure)):
        ids = await db.scalars(
            update(model)
            .where(model.case_id == source.id)
            .values(case_id=target.id)
            .returning(model.id)
        )
        moved[name] = sorted(map(str, ids))
    return moved
