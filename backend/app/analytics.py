"""Dashboard figures and incident response metrics over the cases a person can see.

Metric definitions (each a mean, in hours, over the cases that have both times):
- MTTD: first compromise to detection
- MTTA: case opened to acknowledged
- MTTC: detection to containment
- MTTR: detection to the case closing
- Dwell time: first compromise to eradication (or closing, if eradication was not recorded)

Detection is the detection milestone, else the case's detected time, else when it was opened.
Containment is the containment milestone, else when the case reached its contain SLA milestone.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import fmean
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app import cases, sla
from app.auth import TenantContext
from app.cases import Resolution
from app.models import Case, CaseObservable, PirAction, TimelineEvent
from app.tenant_config import TenantConfig

AGING = ((timedelta(days=1), "under_1_day"), (timedelta(days=7), "1_to_7_days"),
         (timedelta(days=30), "7_to_30_days"))  # fmt: skip
NOT_INCIDENTS = {Resolution.MERGED, Resolution.DUPLICATE}


class Workload(BaseModel):
    owner_id: UUID | None
    open: int


class DashboardOut(BaseModel):
    open_by_severity: dict[str, int]
    open_by_state: dict[str, int]
    sla_status: dict[str, int]  # the worst SLA status of each open case
    sla_compliance: float | None  # share of SLA steps met on time, cases closed in the window
    workload: list[Workload]
    aging: dict[str, int]  # open cases by age
    open_pir_actions: int


class Metric(BaseModel):
    hours: float | None
    cases: int


class MetricsOut(BaseModel):
    since: datetime
    until: datetime
    cases: int
    mttd: Metric
    mtta: Metric
    mttc: Metric
    mttr: Metric
    dwell_time: Metric
    closed: int
    false_positive_rate: float | None
    by_category: dict[str, int]
    by_technique: dict[str, int]
    recurring: int  # cases sharing an observable with a case closed before they opened
    recurrence_rate: float | None


@dataclass
class CaseFacts:
    id: UUID
    created_at: datetime
    category: str
    detected_at: datetime | None = None
    acknowledged_at: datetime | None = None
    contained_at: datetime | None = None
    closed_at: datetime | None = None
    resolution: str | None = None
    techniques: list[str] = field(default_factory=list)
    milestones: dict[str, datetime] = field(default_factory=dict)


def _rate(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _mean(spans: list[tuple[datetime | None, datetime | None]]) -> Metric:
    hours = [(end - start) / timedelta(hours=1) for start, end in spans if start and end]
    return Metric(hours=fmean(hours) if hours else None, cases=len(hours))


def compute(
    facts: list[CaseFacts], recurring: set[UUID], since: datetime, until: datetime
) -> MetricsOut:
    def detection(c: CaseFacts) -> datetime:
        return c.milestones.get("detection") or c.detected_at or c.created_at

    closed = [c for c in facts if c.closed_at and c.resolution not in NOT_INCIDENTS]
    return MetricsOut(
        since=since,
        until=until,
        cases=len(facts),
        mttd=_mean([(c.milestones.get("first_compromise"), detection(c)) for c in facts]),
        mtta=_mean([(c.created_at, c.acknowledged_at) for c in facts]),
        mttc=_mean(
            [(detection(c), c.milestones.get("containment") or c.contained_at) for c in facts]
        ),
        mttr=_mean([(detection(c), c.closed_at) for c in closed]),
        dwell_time=_mean(
            [
                (
                    c.milestones.get("first_compromise"),
                    c.milestones.get("eradication") or c.closed_at,
                )
                for c in facts
            ]
        ),
        closed=len(closed),
        false_positive_rate=_rate(
            sum(c.resolution == Resolution.FALSE_POSITIVE for c in closed), len(closed)
        ),
        by_category=dict(Counter(c.category for c in facts).most_common()),
        by_technique=dict(Counter(t for c in facts for t in c.techniques).most_common()),
        recurring=len(recurring),
        recurrence_rate=_rate(len(recurring), len(facts)),
    )


def sla_compliance(clock_lists: list[list[sla.SlaClock]]) -> float | None:
    decided = [c.status for clocks in clock_lists for c in clocks if c.status != "pending"]
    return _rate(decided.count("met"), len(decided))


async def dashboard(
    db: AsyncSession, ctx: TenantContext, config: TenantConfig, now: datetime, days: int
) -> DashboardOut:
    visible = cases.visible(ctx).where(Case.merged_into_id.is_(None))
    open_cases = (await db.scalars(visible.where(Case.state.not_in(config.terminal_states)))).all()
    recently_closed = await db.scalars(visible.where(Case.closed_at >= now - timedelta(days=days)))
    open_actions = await db.scalar(
        select(func.count())
        .select_from(PirAction)
        .where(
            PirAction.done_at.is_(None),
            PirAction.case_id.in_(cases.visible(ctx).with_only_columns(Case.id)),
        )
    )

    def age(case: Case) -> str:
        return next(
            (name for limit, name in AGING if now - case.created_at < limit), "over_30_days"
        )

    owners = Counter(c.owner_id for c in open_cases)
    return DashboardOut(
        open_by_severity=dict(Counter(c.severity for c in open_cases)),
        open_by_state=dict(Counter(c.state for c in open_cases)),
        sla_status=dict(
            Counter(sla.worst(cases.clocks(c, config, now)) or "none" for c in open_cases)
        ),
        sla_compliance=sla_compliance([cases.clocks(c, config, now) for c in recently_closed]),
        workload=[Workload(owner_id=o, open=n) for o, n in owners.most_common()],
        aging=dict(Counter(age(c) for c in open_cases)),
        open_pir_actions=open_actions or 0,
    )


async def metrics(
    db: AsyncSession, ctx: TenantContext, since: datetime, until: datetime
) -> MetricsOut:
    in_window = cases.visible(ctx).where(
        Case.merged_into_id.is_(None), Case.created_at >= since, Case.created_at < until
    )
    found = (await db.scalars(in_window)).all()
    ids = [c.id for c in found]
    milestones: dict[UUID, dict[str, datetime]] = {}
    for case_id, milestone, at in await db.execute(
        select(TimelineEvent.case_id, TimelineEvent.milestone, TimelineEvent.occurred_at).where(
            TimelineEvent.case_id.in_(ids), TimelineEvent.milestone.is_not(None)
        )
    ):
        milestones.setdefault(case_id, {})[str(milestone)] = at

    current, earlier, shared = aliased(Case), aliased(Case), aliased(CaseObservable)
    recurring = set(
        await db.scalars(
            select(CaseObservable.case_id)
            .distinct()
            .join(shared, shared.observable_id == CaseObservable.observable_id)
            .join(earlier, earlier.id == shared.case_id)
            .join(current, current.id == CaseObservable.case_id)
            .where(
                CaseObservable.case_id.in_(ids),
                earlier.id.in_(cases.visible(ctx).with_only_columns(Case.id)),
                earlier.closed_at < current.created_at,
            )
        )
    )
    facts = [
        CaseFacts(
            id=c.id,
            created_at=c.created_at,
            category=c.category,
            detected_at=c.detected_at,
            acknowledged_at=c.acknowledged_at,
            contained_at=c.contained_at,
            closed_at=c.closed_at,
            resolution=c.resolution,
            techniques=c.attack_techniques,
            milestones=milestones.get(c.id, {}),
        )
        for c in found
    ]
    return compute(facts, recurring, since, until)
