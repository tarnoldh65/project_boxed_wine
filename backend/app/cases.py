"""Cases: creation from templates, intake, updates, merge and split, linked assets."""

import copy
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, Literal, NoReturn, Self
from uuid import UUID

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)
from sqlalchemy import Select, delete, func, or_, select, update
from sqlalchemy import case as sql_case
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import (
    audit,
    escalation,
    evidence,
    journal,
    observable_store,
    pir,
    regulatory,
    sla,
    tasks,
)  # fmt: skip
from app.auth import TenantContext, deny
from app.evidence import EvidenceOut
from app.journal import CommentOut, NoteOut, TimelineOut
from app.models import Alert, Asset, Case, CaseAsset, CaseNumber, Membership, Task, User
from app.observable_store import ObservableOut
from app.pir import PirOut
from app.regulatory import ClockOut, DecisionOut, DisclosureOut
from app.tasks import TaskOut
from app.tenant_config import (
    MAX_TEXT,
    ClockTrigger,
    FieldType,
    Key,
    Label,
    Pap,
    Region,
    Regulated,
    TenantConfig,
    Tlp,
    Trigger,
    UtcDatetime,
)

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
LongText = Annotated[str, StringConstraints(max_length=MAX_TEXT)]
Technique = Annotated[str, StringConstraints(pattern=r"^T\d{4}(\.\d{3})?$")]
Source = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_.-]{0,39}$")]
Ref = Annotated[str, StringConstraints(min_length=1, max_length=200)]


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


class QueueSort(StrEnum):
    NUMBER = "number"
    TITLE = "title"
    STATE = "state"
    SEVERITY = "severity"
    SLA = "sla"
    OWNER = "owner"
    CREATED = "created"


SlaFilter = sla.Status | Literal["none"]


class QueueQuery(BaseModel):
    """Filters, order, and page for the case queue."""

    text: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] = ""
    state: Key | None = None
    # Not in a terminal state and not merged, as the dashboard counts open cases.
    open_only: bool = False
    severity: Key | None = None
    owner: UUID | None = None
    unassigned: bool = False
    sla: SlaFilter | None = None
    age: Literal["under_1_day", "1_to_7_days", "7_to_30_days", "over_30_days"] | None = None
    sort: QueueSort = QueueSort.CREATED
    descending: bool = True
    limit: Annotated[int, Field(ge=1, le=500)] = 100
    offset: Annotated[int, Field(ge=0)] = 0


class CaseQueue(BaseModel):
    cases: list[CaseSummary]
    total: int


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
    legal_hold: bool
    privileged: bool


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


class JiraIssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    issue_key: str
    url: str
    created_by: str
    created_at: datetime


class ConfluencePageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: str
    url: str
    version: int
    published_by: str
    published_at: datetime


class CaseOut(CaseFields):
    merged_into: int | None
    alerts: list[AlertOut]
    assets: list[AssetOut]
    tasks: list[TaskOut]
    sla: list[sla.SlaClock]
    observables: list[ObservableOut]
    evidence: list[EvidenceOut]
    timeline: list[TimelineOut]
    notes: list[NoteOut]
    comments: list[CommentOut]
    clocks: list[ClockOut]
    materiality: list[DecisionOut]
    disclosures: list[DisclosureOut]
    pir: PirOut
    jira: JiraIssueOut | None
    confluence: list[ConfluencePageOut]


class MarkingIn(BaseModel):
    """Turn a legal marking (hold or privilege) on or off, with the reason recorded."""

    on: bool
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class MajorIncidentIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class CaseCreate(BaseModel):
    template: Key
    title: Title
    description: LongText = ""
    impact: Key | None = None
    urgency: Key | None = None
    detected_at: UtcDatetime | None = None


class Override(BaseModel):
    severity: Key
    justification: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
    ]


# Open-case age buckets: each case falls in the first whose limit it is younger than.
AGING = (
    (timedelta(days=1), "under_1_day"),
    (timedelta(days=7), "1_to_7_days"),
    (timedelta(days=30), "7_to_30_days"),
)
MAX_NUMBER_DIGITS = 10  # longer digit strings cannot be a case number
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
    detected_at: UtcDatetime | None = None

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
    detected_at: UtcDatetime | None = None
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
    observable_ids: list[UUID] = []

    @model_validator(mode="after")
    def check_selection(self) -> Self:
        if not (self.alert_ids or self.asset_ids or self.observable_ids):
            raise ValueError("select at least one alert, asset, or observable to split off")
        return self


class AssetIn(BaseModel):
    kind: AssetKind
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    external_id: Ref | None = None


# --- Queries ------------------------------------------------------------------------------


def visible(ctx: TenantContext) -> Select[Case]:
    """Cases the principal may see; restricted cases are hidden without the permission.

    The search_hits database function (migration 0011) applies the same rule.
    """
    query = select(Case)
    if "restricted_case" not in ctx.permissions:
        query = query.where(Case.restricted.is_(False))
    return query


def like_pattern(text: str) -> str:
    """An ILIKE pattern that finds `text` literally anywhere in a value."""
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _queue_filters(
    ctx: TenantContext, config: TenantConfig, q: QueueQuery, now: datetime
) -> Select:
    query = visible(ctx)
    if q.text:
        matches = Case.title.ilike(like_pattern(q.text))
        if q.text.isdigit() and len(q.text) < MAX_NUMBER_DIGITS:
            matches = or_(matches, Case.number == int(q.text))
        query = query.where(matches)
    if q.open_only:
        query = query.where(
            Case.state.not_in(config.terminal_states), Case.merged_into_id.is_(None)
        )
    if q.state:
        query = query.where(Case.state == q.state)
    if q.severity:
        query = query.where(Case.severity == q.severity)
    if q.unassigned:
        query = query.where(Case.owner_id.is_(None))
    if q.owner:
        query = query.where(Case.owner_id == q.owner)
    if q.age:
        limits = [limit for limit, _ in AGING]
        names = [name for _, name in AGING]
        index = names.index(q.age) if q.age in names else len(limits)
        if index < len(limits):
            query = query.where(Case.created_at > now - limits[index])
        if index > 0:
            query = query.where(Case.created_at <= now - limits[index - 1])
    return query


def _queue_order(query: Select, config: TenantConfig, q: QueueQuery) -> Select:
    def rank(order: list[str], column: Any) -> Any:
        return sql_case({key: i for i, key in enumerate(order)}, value=column, else_=len(order))

    newest_first = Case.number.desc()
    if q.sort == QueueSort.SLA:  # computed, so sorted after loading
        return query.order_by(newest_first)
    if q.sort == QueueSort.OWNER:
        query = query.outerjoin(User, User.id == Case.owner_id)
    column = {
        QueueSort.NUMBER: Case.number,
        QueueSort.TITLE: Case.title,
        QueueSort.STATE: rank([s.key for s in config.states], Case.state),
        QueueSort.SEVERITY: rank([s.key for s in config.severity.levels], Case.severity),
        QueueSort.OWNER: User.display_name,
        QueueSort.CREATED: Case.created_at,
    }[q.sort]
    return query.order_by(
        (column.desc() if q.descending else column.asc()).nulls_last(), newest_first
    )


async def queue(
    db: AsyncSession, ctx: TenantContext, config: TenantConfig, q: QueueQuery
) -> CaseQueue:
    """One page of the cases matching a filter, and how many match in all."""
    now = datetime.now(UTC)
    filtered = _queue_filters(ctx, config, q, now)
    ordered = _queue_order(filtered, config, q)
    if not q.sla and q.sort != QueueSort.SLA:
        total = await db.scalar(select(func.count()).select_from(filtered.subquery()))
        page = await db.scalars(ordered.limit(q.limit).offset(q.offset))
        return CaseQueue(cases=[summary(c, config, now) for c in page], total=total or 0)

    # SLA status is computed from each case's clocks, so these run over the matching cases.
    found = [summary(c, config, now) for c in await db.scalars(ordered)]
    if q.sla:
        found = [c for c in found if (c.sla_status or "none") == q.sla]
    if q.sort == QueueSort.SLA:
        order = {status: i for i, status in enumerate(sla.SEVERITY_ORDER)}
        found.sort(
            key=lambda c: order.get(c.sla_status, len(order)) if c.sla_status else len(order),
            reverse=q.descending,
        )
    return CaseQueue(cases=found[q.offset : q.offset + q.limit], total=len(found))


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


async def case_out(
    db: AsyncSession, ctx: TenantContext, case: Case, config: TenantConfig
) -> CaseOut:
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
    now = datetime.now(UTC)
    case_clocks = clocks(case, config, now)
    from app import outbound  # outbound builds on this module

    jira, pages = await outbound.for_case(db, case)
    return CaseOut(
        **CaseFields.model_validate(case).model_dump(exclude={"sla_status"}),
        sla_status=sla.worst(case_clocks),
        merged_into=merged_into,
        alerts=[AlertOut.model_validate(a) for a in alerts],
        assets=[AssetOut.model_validate(a) for a in assets],
        tasks=tasks.task_outs(await tasks.case_tasks(db, case.id)),
        sla=case_clocks,
        observables=await observable_store.for_case(db, case, "restricted_case" in ctx.permissions),
        evidence=await evidence.for_case(db, case),
        timeline=await journal.timeline(db, case),
        notes=await journal.notes(db, case),
        comments=await journal.comments(db, case),
        clocks=await regulatory.clocks(db, case, now),
        materiality=await regulatory.decisions(db, case),
        disclosures=await regulatory.disclosures(db, case),
        pir=await pir.for_case(db, case),
        jira=jira,
        confluence=pages,
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
    await journal.record(db, case, actor, f"Case opened from the {template.name} template")
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
    await journal.record(db, case, actor, f"Alert received from {alert.source}: {alert.title}")
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
    before = {key: getattr(case, key) for key in TRACKED}
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
    for line in _changes(config, member_list, before, case, fields):
        await journal.record(db, case, ctx.principal.actor, line)
    await ctx.audit(
        db,
        "case.update",
        f"case:{case.number}",
        changes=change.model_dump(mode="json", include=fields),
    )
    if fields & {"regulated_data", "data_regions"}:
        started = await regulatory.start(
            db, config, case, ClockTrigger.REGULATED_DATA, case.updated_at
        )
        if started:
            await journal.record(db, case, ctx.principal.actor, clocks_started(config, started))
            await ctx.audit(db, "clock.start", f"case:{case.number}", regimes=started)
    if not terminal:
        await tasks.sync_playbook(db, case, config, ctx.principal.actor)


# Case fields whose changes go on the timeline; the rest show as "Details edited".
TRACKED = (
    "state", "resolution", "severity", "owner_id", "team", "restricted", "regulated_data",
    "data_regions",
)  # fmt: skip


def _label(field: str) -> str:
    return field.removesuffix("_id").replace("_", " ")


def _changes(
    config: TenantConfig,
    member_list: Sequence[User],
    before: dict[str, Any],
    case: Case,
    fields: set[str],
) -> list[str]:
    """Timeline lines for what an update changed."""
    state_name = {s.key: s.name for s in config.states}
    lines = []
    if case.state != before["state"]:
        line = f"State changed from {state_name[before['state']]} to {state_name[case.state]}"
        if case.resolution is not None:
            line += f", resolved as {case.resolution.replace('_', ' ')}"
        lines.append(line)
    if case.severity != before["severity"]:
        lines.append(f"Severity changed from {before['severity']} to {case.severity}")
    if case.owner_id != before["owner_id"]:
        owner = next((m.display_name for m in member_list if m.id == case.owner_id), None)
        lines.append(f"Assigned to {owner}" if owner else "Unassigned")
    if case.team != before["team"]:
        team = config.team(case.team) if case.team else None
        lines.append(f"Team changed to {team.name}" if team else "Team removed")
    if case.restricted != before["restricted"]:
        lines.append("Marked restricted" if case.restricted else "Restriction removed")
    if (case.regulated_data, case.data_regions) != (
        before["regulated_data"],
        before["data_regions"],
    ):
        flagged = ", ".join(d.upper() for d in case.regulated_data) or "none"
        regions = f" ({', '.join(case.data_regions)})" if case.data_regions else ""
        lines.append(f"Regulated data: {flagged}{regions}")
    edited = sorted(_label(f) for f in fields - set(TRACKED) - {"impact", "urgency", "override"})
    if edited:
        lines.append(f"Details edited: {', '.join(edited)}")
    return lines


def clocks_started(config: TenantConfig, keys: Sequence[str]) -> str:
    names = [r.name for r in config.regimes if r.key in keys]
    return f"Notification {'clock' if len(names) == 1 else 'clocks'} started: {', '.join(names)}"


async def _check_gate(db: AsyncSession, config: TenantConfig, case: Case) -> None:
    state = next(s for s in config.states if s.key == case.state)
    if state.require_tasks_done:
        unfinished = [
            t.title for t in await tasks.case_tasks(db, case.id) if t.status not in tasks.FINISHED
        ]
        if unfinished:
            invalid(f"finish or waive every task first: {'; '.join(unfinished)}", "state")
    if (
        state.require_pir
        and case.resolution == Resolution.TRUE_POSITIVE
        and not pir.complete(await pir.get(db, case))
    ):
        invalid(
            "complete the post-incident review first: root cause, contributing factors, "
            "what went well, and what did not",
            "state",
        )


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
    await journal.record(db, case, ctx.principal.actor, "Acknowledged")
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
    await journal.record(db, case, ctx.principal.actor, f"Major incident declared: {reason}")
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
    moved_observables = await observable_store.move(db, source, target, None)
    moved_evidence = await evidence.move(db, source, target)
    moved_journal = await journal.move(db, source, target)
    moved_regulatory = await regulatory.move(db, source, target)
    from app import outbound

    moved_jira = await outbound.move_issue(db, source, target)
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
        observables=moved_observables,
        evidence=moved_evidence,
        **moved_journal,
        **moved_regulatory,
        jira_issue=moved_jira,
    )
    await ctx.audit(db, "case.absorb", f"case:{target.number}", source=source.number)
    await journal.record(db, source, ctx.principal.actor, f"Merged into case #{target.number}")
    await journal.record(
        db, target, ctx.principal.actor, f"Case #{source.number} merged into this case"
    )


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
    if not set(body.observable_ids) <= await observable_store.own_ids(db, source):
        invalid("observables must be linked to the case", "observable_ids")

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
    observables = await observable_store.move(db, source, new, body.observable_ids)
    await ctx.audit(
        db,
        "case.split",
        f"case:{source.number}",
        to=new.number,
        alerts=alerts,
        assets=assets,
        observables=observables,
    )
    await ctx.audit(db, "case.create", f"case:{new.number}", split_from=source.number)
    await journal.record(db, source, ctx.principal.actor, f"Split into case #{new.number}")
    await journal.record(db, new, ctx.principal.actor, f"Split from case #{source.number}")
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
    await journal.record(
        db,
        case,
        ctx.principal.actor,
        f"Asset linked: {asset.kind.replace('_', ' ')} {asset.name}",
        asset_id=asset.id,
    )
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
    asset = await db.get(Asset, asset_id)
    assert asset is not None  # noqa: S101 - a linked asset exists
    await journal.record(
        db,
        case,
        ctx.principal.actor,
        f"Asset unlinked: {asset.kind.replace('_', ' ')} {asset.name}",
        asset_id=asset_id,
    )
    await ctx.audit(db, "case.asset_unlink", f"case:{case.number}", asset=str(asset_id))
