"""Cases: creation from templates, intake, updates, merge and split, linked assets."""

import copy
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, NoReturn, Self
from uuid import UUID

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    StringConstraints,
    model_validator,
)
from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit, escalation, sla, tasks
from app.auth import TenantContext, deny
from app.models import Alert, Asset, Case, CaseAsset, CaseNumber, Membership, Task, User
from app.tasks import TaskOut
from app.tenant_config import MAX_TEXT, FieldType, Key, Label, Pap, TenantConfig, Tlp, Trigger

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
LongText = Annotated[str, StringConstraints(max_length=MAX_TEXT)]
Region = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9-]{1,9}$")]
Technique = Annotated[str, StringConstraints(pattern=r"^T\d{4}(\.\d{3})?$")]
Source = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_.-]{0,39}$")]
Ref = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class Regulated(StrEnum):
    PII = "pii"
    PHI = "phi"
    PCI = "pci"
    CUI = "cui"


class Resolution(StrEnum):
    TRUE_POSITIVE = "true_positive"
    FALSE_POSITIVE = "false_positive"
    BENIGN = "benign"
    DUPLICATE = "duplicate"
    MERGED = "merged"


class AssetKind(StrEnum):
    HOST = "host"
    ACCOUNT = "account"
    APPLICATION = "application"
    DATA_SET = "data_set"


def invalid(message: str, *loc: str | int) -> NoReturn:
    """Reject a request in the same shape as FastAPI's own validation errors."""
    raise RequestValidationError([{"loc": ("body", *loc), "msg": message, "type": "value_error"}])


# --- API shapes ---------------------------------------------------------------------------


class CaseSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    number: int
    title: str
    state: str
    category: str
    severity: str
    priority: str
    team: str | None
    owner_id: UUID | None
    restricted: bool
    major_incident_at: datetime | None
    created_at: datetime
    updated_at: datetime
    sla_status: sla.Status | None = None


class CaseFields(CaseSummary):
    id: UUID
    description: str
    impact: str
    urgency: str
    override_justification: str | None
    source: str
    tlp: Tlp
    pap: Pap
    regulated_data: list[Regulated]
    data_regions: list[str]
    business_units: list[str]
    attack_techniques: list[str]
    custom: dict[str, Any]
    resolution: Resolution | None
    detected_at: datetime | None
    closed_at: datetime | None
    acknowledged_at: datetime | None
    triaged_at: datetime | None
    contained_at: datetime | None
    major_incident_reason: str | None


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source: str
    external_id: str | None
    group_key: str | None
    title: str
    payload: dict[str, Any]
    received_at: datetime


class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: AssetKind
    name: str
    external_id: str | None


class CaseOut(CaseFields):
    merged_into: int | None
    alerts: list[AlertOut]
    assets: list[AssetOut]
    tasks: list[TaskOut]
    sla: list[sla.SlaClock]


class MajorIncidentIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class CaseCreate(BaseModel):
    template: Key
    title: Title
    description: LongText = ""
    impact: Key | None = None
    urgency: Key | None = None
    detected_at: AwareDatetime | None = None


class Override(BaseModel):
    severity: Key
    justification: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
    ]


NULLABLE = {"override", "owner_id", "team", "resolution", "detected_at"}


class CaseUpdate(BaseModel):
    """Fields to change; omitted fields stay as they are."""

    title: Title | None = None
    description: LongText | None = None
    state: Key | None = None
    impact: Key | None = None
    urgency: Key | None = None
    override: Override | None = None
    owner_id: UUID | None = None
    team: Key | None = None
    tlp: Tlp | None = None
    pap: Pap | None = None
    restricted: bool | None = None
    regulated_data: list[Regulated] | None = None
    data_regions: list[Region] | None = None
    business_units: list[Label] | None = None
    attack_techniques: list[Technique] | None = None
    custom: dict[str, Any] | None = None
    resolution: Resolution | None = None
    detected_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def check_nulls(self) -> Self:
        if nulls := sorted(k for k in self.model_fields_set - NULLABLE if getattr(self, k) is None):
            raise ValueError(f"cannot be null: {', '.join(nulls)}")
        return self


class AlertIn(BaseModel):
    template: Key
    title: Title
    description: LongText = ""
    source: Source
    external_id: Ref | None = None
    # Alerts sharing a group key join the same open case.
    group_key: Ref | None = None
    detected_at: AwareDatetime | None = None
    payload: dict[str, Any] = {}


class IntakeOut(BaseModel):
    alert_id: UUID
    case_number: int
    new_case: bool
    duplicate: bool


class MergeIn(BaseModel):
    into: int


class SplitIn(BaseModel):
    title: Title
    alert_ids: list[UUID] = []
    asset_ids: list[UUID] = []

    @model_validator(mode="after")
    def check_selection(self) -> Self:
        if not self.alert_ids and not self.asset_ids:
            raise ValueError("select at least one alert or asset to split off")
        return self


class AssetIn(BaseModel):
    kind: AssetKind
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    external_id: Ref | None = None


# --- Queries ------------------------------------------------------------------------------


def visible(ctx: TenantContext) -> Select[Case]:
    """Cases the principal may see; restricted cases are hidden without the permission."""
    query = select(Case)
    if "restricted_case" not in ctx.permissions:
        query = query.where(Case.restricted.is_(False))
    return query


async def get_case(db: AsyncSession, ctx: TenantContext, number: int) -> Case:
    case = await db.scalar(visible(ctx).where(Case.number == number))
    if case is None:
        raise HTTPException(404, "Case not found")
    return case


async def members(db: AsyncSession, tenant_id: UUID) -> Sequence[User]:
    in_tenant = select(Membership.user_id).where(Membership.tenant_id == tenant_id)
    return (
        await db.scalars(select(User).where(User.id.in_(in_tenant)).order_by(User.display_name))
    ).all()


def clocks(case: Case, config: TenantConfig, now: datetime) -> list[sla.SlaClock]:
    level = next((lv for lv in config.severity.levels if lv.key == case.severity), None)
    if level is None:
        return []  # the severity was removed from the configuration
    met: dict[sla.Step, datetime | None] = {
        "acknowledge": case.acknowledged_at,
        "triage": case.triaged_at,
        "contain": case.contained_at,
        "resolve": case.closed_at,
    }
    return sla.clocks(level.sla, case.created_at, met, now)


def summary(case: Case, config: TenantConfig, now: datetime) -> CaseSummary:
    status = sla.worst(clocks(case, config, now))
    return CaseSummary.model_validate(case).model_copy(update={"sla_status": status})


async def case_out(db: AsyncSession, case: Case, config: TenantConfig) -> CaseOut:
    alerts = await db.scalars(
        select(Alert).where(Alert.case_id == case.id).order_by(Alert.received_at)
    )
    assets = await db.scalars(
        select(Asset)
        .join(CaseAsset, CaseAsset.asset_id == Asset.id)
        .where(CaseAsset.case_id == case.id)
        .order_by(Asset.kind, Asset.name)
    )
    merged_into = None
    if case.merged_into_id is not None:
        merged_into = await db.scalar(select(Case.number).where(Case.id == case.merged_into_id))
    case_clocks = clocks(case, config, datetime.now(UTC))
    return CaseOut(
        **CaseFields.model_validate(case).model_dump(exclude={"sla_status"}),
        sla_status=sla.worst(case_clocks),
        merged_into=merged_into,
        alerts=[AlertOut.model_validate(a) for a in alerts],
        assets=[AssetOut.model_validate(a) for a in assets],
        tasks=tasks.task_outs(await tasks.case_tasks(db, case.id)),
        sla=case_clocks,
    )


# --- Commands -----------------------------------------------------------------------------


async def _next_number(db: AsyncSession, tenant_id: UUID) -> int:
    stmt = insert(CaseNumber).values(tenant_id=tenant_id, last=1)
    stmt = stmt.on_conflict_do_update(
        index_elements=["tenant_id"], set_={"last": CaseNumber.last + 1}
    ).returning(CaseNumber.last)
    return (await db.execute(stmt)).scalar_one()


def _rate(config: TenantConfig, impact: str, urgency: str) -> str:
    if impact not in config.severity.impacts:
        invalid("unknown impact", "impact")
    if urgency not in config.severity.urgencies:
        invalid("unknown urgency", "urgency")
    return config.severity.severity(impact, urgency)


async def create_case(
    db: AsyncSession,
    tenant_id: UUID,
    config: TenantConfig,
    actor: str,
    body: CaseCreate,
    source: str = "manual",
) -> Case:
    template = config.template(body.template) or invalid("unknown template", "template")
    impact = body.impact or template.impact
    urgency = body.urgency or template.urgency
    severity = _rate(config, impact, urgency)
    team = config.team(template.team)
    case = Case(
        tenant_id=tenant_id,
        number=await _next_number(db, tenant_id),
        title=body.title,
        description=body.description,
        state=config.states[0].key,
        category=template.key,
        impact=impact,
        urgency=urgency,
        severity=severity,
        priority=config.severity.priority(severity),
        team=template.team,
        owner_id=team.on_call if team else None,
        source=source,
        tlp=template.tlp,
        pap=template.pap,
        restricted=False,
        regulated_data=[],
        data_regions=[],
        business_units=[],
        attack_techniques=[],
        custom={},
        detected_at=body.detected_at,
    )
    db.add(case)
    await db.flush()
    await audit.record(
        db,
        actor,
        "case.create",
        f"case:{case.number}",
        tenant_id,
        {"template": template.key, "source": source},
    )
    await tasks.sync_playbook(db, case, config, actor)
    await escalation.fire(db, config, case, Trigger.CASE_CREATED)
    return case


async def ingest(
    db: AsyncSession, tenant_id: UUID, config: TenantConfig, actor: str, alert: AlertIn
) -> IntakeOut:
    """Store an alert, attaching it to an open case in its group or opening a new case."""
    if alert.external_id is not None:
        existing = await db.scalar(
            select(Alert).where(
                Alert.source == alert.source, Alert.external_id == alert.external_id
            )
        )
        if existing is not None:
            number = await db.scalar(select(Case.number).where(Case.id == existing.case_id))
            assert number is not None  # noqa: S101 - every alert belongs to a case
            return IntakeOut(
                alert_id=existing.id, case_number=number, new_case=False, duplicate=True
            )

    case = None
    if alert.group_key is not None:
        case = await db.scalar(
            select(Case)
            .join(Alert, Alert.case_id == Case.id)
            .where(Alert.group_key == alert.group_key, Case.state.not_in(config.terminal_states))
            .order_by(Alert.received_at.desc())
            .limit(1)
        )
    new_case = case is None
    if case is None:
        create = CaseCreate.model_validate(alert.model_dump(include=set(CaseCreate.model_fields)))
        case = await create_case(db, tenant_id, config, actor, create, source=alert.source)

    row = Alert(
        tenant_id=tenant_id,
        case_id=case.id,
        source=alert.source,
        external_id=alert.external_id,
        group_key=alert.group_key,
        title=alert.title,
        payload=alert.payload,
    )
    db.add(row)
    await db.flush()
    await audit.record(
        db,
        actor,
        "alert.ingest",
        f"alert:{row.id}",
        tenant_id,
        {"case": case.number, "source": alert.source},
    )
    return IntakeOut(alert_id=row.id, case_number=case.number, new_case=new_case, duplicate=False)


async def update_case(
    db: AsyncSession, ctx: TenantContext, config: TenantConfig, case: Case, change: CaseUpdate
) -> None:
    fields = change.model_fields_set
    if "restricted" in fields and "restricted_case" not in ctx.permissions:
        await deny(db, ctx.principal, f"case:{case.number}:restricted", ctx.tenant.id)
    if change.state is not None and change.state not in config.state_keys:
        invalid("unknown state", "state")
    if change.team is not None and config.team(change.team) is None:
        invalid("unknown team", "team")
    if change.resolution is Resolution.MERGED:
        invalid("cases are marked merged by merging them", "resolution")
    member_list = await members(db, ctx.tenant.id)
    if change.owner_id is not None and change.owner_id not in {m.id for m in member_list}:
        invalid("owner must be a tenant member", "owner_id")

    was_terminal = case.state in config.terminal_states
    previous_state = case.state
    for key in fields - {"override"}:
        setattr(case, key, getattr(change, key))

    if fields & {"impact", "urgency", "override"}:
        rated = _rate(config, case.impact, case.urgency)
        if "override" in fields:
            case.override_justification = change.override and change.override.justification
        if change.override is not None:
            if change.override.severity not in config.severity.keys:
                invalid("unknown severity", "override", "severity")
            case.severity = change.override.severity
        elif case.override_justification is None:
            case.severity = rated
        case.priority = config.severity.priority(case.severity)

    terminal = case.state in config.terminal_states
    if terminal and case.resolution is None:
        invalid("closing a case needs a resolution", "resolution")
    if not terminal and case.resolution is not None:
        if was_terminal:
            case.resolution = None  # reopened
        else:
            invalid("only closed cases have a resolution", "resolution")
    if terminal != was_terminal:
        case.closed_at = datetime.now(UTC) if terminal else None
    if case.state != previous_state:
        await _check_gate(db, config, case)
        _record_milestones(case, config)

    for key, message in config.validate_values(case.custom, case.state).items():
        invalid(message, "custom", key)
    usernames = {m.username for m in member_list}
    for field in config.custom_fields:
        value = case.custom.get(field.key)
        if field.type is FieldType.USER and value and value not in usernames:
            invalid("not a tenant member", "custom", field.key)

    case.updated_at = datetime.now(UTC)
    await ctx.audit(
        db,
        "case.update",
        f"case:{case.number}",
        changes=change.model_dump(mode="json", include=fields),
    )
    if not terminal:
        await tasks.sync_playbook(db, case, config, ctx.principal.actor)


async def _check_gate(db: AsyncSession, config: TenantConfig, case: Case) -> None:
    state = next(s for s in config.states if s.key == case.state)
    if state.require_tasks_done:
        unfinished = [
            t.title for t in await tasks.case_tasks(db, case.id) if t.status not in tasks.FINISHED
        ]
        if unfinished:
            invalid(f"finish or waive every task first: {'; '.join(unfinished)}", "state")


def _record_milestones(case: Case, config: TenantConfig) -> None:
    """Meet SLA milestones the case has now reached; closing meets them all."""
    now = datetime.now(UTC)
    reached = config.state_keys.index(case.state)
    milestones = {s.sla_milestone for s in config.states[: reached + 1]}
    if case.state in config.terminal_states:
        milestones |= {"triage", "contain"}
    # Working a case past its first state acknowledges it.
    if reached > 0:
        case.acknowledged_at = case.acknowledged_at or now
    if "triage" in milestones:
        case.triaged_at = case.triaged_at or now
    if "contain" in milestones:
        case.contained_at = case.contained_at or now


async def acknowledge(db: AsyncSession, ctx: TenantContext, case: Case) -> None:
    if case.acknowledged_at is not None:
        invalid("the case is already acknowledged")
    case.acknowledged_at = case.updated_at = datetime.now(UTC)
    case.owner_id = case.owner_id or ctx.principal.user_id
    await ctx.audit(db, "case.acknowledge", f"case:{case.number}")


async def declare_major_incident(
    db: AsyncSession, ctx: TenantContext, config: TenantConfig, case: Case, reason: str
) -> None:
    if case.major_incident_at is not None:
        invalid("a major incident is already declared", "reason")
    if case.state in config.terminal_states:
        invalid("closed cases cannot be declared major incidents", "reason")
    case.major_incident_at = case.updated_at = datetime.now(UTC)
    case.major_incident_reason = reason
    await ctx.audit(db, "case.major_incident", f"case:{case.number}", reason=reason)
    await escalation.fire(db, config, case, Trigger.MAJOR_INCIDENT)


async def _link_assets(db: AsyncSession, case: Case, asset_ids: Sequence[UUID]) -> None:
    if asset_ids:
        await db.execute(
            insert(CaseAsset)
            .values(
                [
                    {"case_id": case.id, "asset_id": a, "tenant_id": case.tenant_id}
                    for a in asset_ids
                ]
            )
            .on_conflict_do_nothing()
        )


async def _move(
    db: AsyncSession,
    source: Case,
    target: Case,
    alert_ids: Sequence[UUID] | None,
    asset_ids: Sequence[UUID] | None,
) -> tuple[list[str], list[str]]:
    """Move alerts and asset links from source to target; None moves everything."""
    alerts = update(Alert).where(Alert.case_id == source.id)
    if alert_ids is not None:
        alerts = alerts.where(Alert.id.in_(alert_ids))
    moved_alerts = (await db.scalars(alerts.values(case_id=target.id).returning(Alert.id))).all()
    links = delete(CaseAsset).where(CaseAsset.case_id == source.id)
    if asset_ids is not None:
        links = links.where(CaseAsset.asset_id.in_(asset_ids))
    moved_assets = (await db.scalars(links.returning(CaseAsset.asset_id))).all()
    await _link_assets(db, target, moved_assets)
    now = datetime.now(UTC)
    source.updated_at = target.updated_at = now
    return sorted(map(str, moved_alerts)), sorted(map(str, moved_assets))


async def _move_tasks(db: AsyncSession, source: Case, target: Case) -> list[str]:
    # Moved tasks no longer stand for the target's own playbook steps.
    moved = await db.scalars(
        update(Task)
        .where(Task.case_id == source.id)
        .values(case_id=target.id, playbook_key=None)
        .returning(Task.id)
    )
    return sorted(map(str, moved))


async def merge(
    db: AsyncSession, ctx: TenantContext, config: TenantConfig, source: Case, target: Case
) -> None:
    if source.id == target.id:
        invalid("a case cannot be merged into itself", "into")
    if source.state in config.terminal_states or target.state in config.terminal_states:
        invalid("only open cases can be merged", "into")
    if source.restricted and not target.restricted:
        invalid("a restricted case can only merge into a restricted case", "into")
    alerts, assets = await _move(db, source, target, None, None)
    moved_tasks = await _move_tasks(db, source, target)
    source.state = config.terminal_states[0]
    source.resolution = Resolution.MERGED
    source.merged_into_id = target.id
    source.closed_at = datetime.now(UTC)
    await ctx.audit(
        db,
        "case.merge",
        f"case:{source.number}",
        into=target.number,
        alerts=alerts,
        assets=assets,
        tasks=moved_tasks,
    )
    await ctx.audit(db, "case.absorb", f"case:{target.number}", source=source.number)


COPIED_ON_SPLIT = (
    "category", "impact", "urgency", "severity", "priority", "override_justification", "team",
    "owner_id", "tlp", "pap", "restricted", "regulated_data", "data_regions", "business_units",
    "attack_techniques", "custom", "detected_at",
)  # fmt: skip


async def split(
    db: AsyncSession, ctx: TenantContext, config: TenantConfig, source: Case, body: SplitIn
) -> Case:
    if source.state in config.terminal_states:
        invalid("only open cases can be split")
    own_alerts = set(await db.scalars(select(Alert.id).where(Alert.case_id == source.id)))
    if not set(body.alert_ids) <= own_alerts:
        invalid("alerts must belong to the case", "alert_ids")
    own_assets = set(
        await db.scalars(select(CaseAsset.asset_id).where(CaseAsset.case_id == source.id))
    )
    if not set(body.asset_ids) <= own_assets:
        invalid("assets must be linked to the case", "asset_ids")

    new = Case(
        tenant_id=source.tenant_id,
        number=await _next_number(db, source.tenant_id),
        title=body.title,
        description="",
        state=config.states[0].key,
        source="split",
        **{key: copy.copy(getattr(source, key)) for key in COPIED_ON_SPLIT},
    )
    db.add(new)
    await db.flush()
    alerts, assets = await _move(db, source, new, body.alert_ids, body.asset_ids)
    await ctx.audit(
        db, "case.split", f"case:{source.number}", to=new.number, alerts=alerts, assets=assets
    )
    await ctx.audit(db, "case.create", f"case:{new.number}", split_from=source.number)
    return new


async def link_asset(db: AsyncSession, ctx: TenantContext, case: Case, body: AssetIn) -> Asset:
    stmt = insert(Asset).values(
        tenant_id=ctx.tenant.id, kind=body.kind, name=body.name, external_id=body.external_id
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["tenant_id", "kind", "name"],
        set_={"external_id": func.coalesce(stmt.excluded.external_id, Asset.external_id)},
    ).returning(Asset)
    asset = (await db.scalars(stmt)).one()
    await _link_assets(db, case, [asset.id])
    case.updated_at = datetime.now(UTC)
    await ctx.audit(
        db,
        "case.asset_link",
        f"case:{case.number}",
        asset=str(asset.id),
        kind=asset.kind,
        name=asset.name,
    )
    return asset


async def unlink_asset(db: AsyncSession, ctx: TenantContext, case: Case, asset_id: UUID) -> None:
    removed = await db.scalar(
        delete(CaseAsset)
        .where(CaseAsset.case_id == case.id, CaseAsset.asset_id == asset_id)
        .returning(CaseAsset.asset_id)
    )
    if removed is None:
        raise HTTPException(404, "Asset not linked to this case")
    case.updated_at = datetime.now(UTC)
    await ctx.audit(db, "case.asset_unlink", f"case:{case.number}", asset=str(asset_id))
