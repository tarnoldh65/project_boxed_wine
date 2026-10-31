"""Per-tenant configuration: lifecycle, fields, severity and SLAs, escalation, teams, templates."""

from datetime import date
from enum import StrEnum
from math import isfinite
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    EmailStr,
    Field,
    HttpUrl,
    PositiveInt,
    StringConstraints,
    model_validator,
)
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TenantConfigRow

Key = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=40)]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
MAX_TEXT = 10_000


def _unique(values: list[str], what: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"duplicate {what}")


class State(BaseModel):
    key: Key
    name: Label
    terminal: bool = False
    # Entering this state (or any later one) meets the triage or contain SLA.
    sla_milestone: Literal["triage", "contain"] | None = None
    # Gate: a case can only enter this state once every task is done or waived.
    require_tasks_done: bool = False


class FieldType(StrEnum):
    TEXT = "text"
    PICKLIST = "picklist"
    DATE = "date"
    USER = "user"
    NUMBER = "number"


class CustomField(BaseModel):
    key: Key
    label: Label
    type: FieldType
    options: list[Label] = []
    # The field must be filled once a case reaches this state or any later one.
    required_from: Key | None = None

    @model_validator(mode="after")
    def check_options(self) -> Self:
        if self.type is FieldType.PICKLIST:
            if not self.options:
                raise ValueError(f"picklist field {self.key} needs options")
            _unique(self.options, f"options in {self.key}")
        elif self.options:
            raise ValueError(f"only picklist fields have options ({self.key})")
        return self

    def accepts(self, value: Any) -> bool:
        match self.type:
            case FieldType.TEXT | FieldType.USER:
                return isinstance(value, str) and len(value) <= MAX_TEXT
            case FieldType.PICKLIST:
                return value in self.options
            case FieldType.NUMBER:
                return (
                    isinstance(value, int | float)
                    and not isinstance(value, bool)
                    and isfinite(value)
                )
            case FieldType.DATE:
                try:
                    date.fromisoformat(value)
                except TypeError, ValueError:
                    return False
                return True


class SlaTarget(BaseModel):
    acknowledge_minutes: PositiveInt
    triage_minutes: PositiveInt
    contain_minutes: PositiveInt
    resolve_minutes: PositiveInt

    @model_validator(mode="after")
    def check_order(self) -> Self:
        steps = [
            self.acknowledge_minutes,
            self.triage_minutes,
            self.contain_minutes,
            self.resolve_minutes,
        ]
        if steps != sorted(steps):
            raise ValueError("SLA targets must not decrease from acknowledge to resolve")
        return self


class SeverityLevel(BaseModel):
    key: Key
    sla: SlaTarget


class Severity(BaseModel):
    """Severity comes from impact x urgency; priority P1 is the most severe level."""

    levels: list[SeverityLevel] = Field(min_length=1)  # most severe first
    impacts: list[Key] = Field(min_length=1)
    urgencies: list[Key] = Field(min_length=1)
    matrix: list[list[Key]]  # matrix[impact][urgency]

    @model_validator(mode="after")
    def check_matrix(self) -> Self:
        _unique(self.keys, "severity levels")
        _unique(self.impacts, "impacts")
        _unique(self.urgencies, "urgencies")
        if len(self.matrix) != len(self.impacts) or any(
            len(row) != len(self.urgencies) for row in self.matrix
        ):
            raise ValueError("the matrix needs one row per impact and one column per urgency")
        if unknown := {cell for row in self.matrix for cell in row} - set(self.keys):
            raise ValueError(f"unknown severities in matrix: {', '.join(sorted(unknown))}")
        return self

    @property
    def keys(self) -> list[str]:
        return [level.key for level in self.levels]

    def severity(self, impact: str, urgency: str) -> str:
        return self.matrix[self.impacts.index(impact)][self.urgencies.index(urgency)]

    def priority(self, severity: str) -> str:
        return f"P{self.keys.index(severity) + 1}"


class Trigger(StrEnum):
    CASE_CREATED = "case_created"
    SLA_WARNING = "sla_warning"
    SLA_BREACH = "sla_breach"
    MAJOR_INCIDENT = "major_incident"


class EscalationRule(BaseModel):
    name: Label
    trigger: Trigger
    severities: list[Key] = []  # empty means every severity
    notify_emails: list[EmailStr] = []
    webhook_url: HttpUrl | None = None
    # Hand the case to this team and its on-call member.
    reassign_to_team: Key | None = None

    @model_validator(mode="after")
    def check_action(self) -> Self:
        if not self.notify_emails and self.webhook_url is None and self.reassign_to_team is None:
            raise ValueError(f"rule {self.name!r} needs an email, a webhook, or a reassignment")
        if self.webhook_url is not None and self.webhook_url.scheme != "https":
            raise ValueError(f"rule {self.name!r} webhook must use https")
        return self


class Tlp(StrEnum):
    CLEAR = "clear"
    GREEN = "green"
    AMBER = "amber"
    AMBER_STRICT = "amber+strict"
    RED = "red"


class Pap(StrEnum):
    CLEAR = "clear"
    GREEN = "green"
    AMBER = "amber"
    RED = "red"


class Team(BaseModel):
    key: Key
    name: Label
    on_call: UUID | None = None  # user id; new intake cases are assigned to them


# Case fields a playbook condition can test, besides custom field keys.
CONDITION_FIELDS = {"severity", "impact", "urgency", "tlp", "pap", "team"}


class Condition(BaseModel):
    field: Key
    value: Annotated[str, StringConstraints(min_length=1, max_length=200)]


class PlaybookTask(BaseModel):
    key: Key
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    description: Annotated[str, StringConstraints(max_length=MAX_TEXT)] = ""
    due_hours: PositiveInt | None = None
    depends_on: list[Key] = []  # keys of earlier tasks in the playbook
    when: Condition | None = None  # created once the case matches; always created if None


class Template(BaseModel):
    """An incident type: the defaults a new case of this category starts with."""

    key: Key
    name: Label
    impact: Key
    urgency: Key
    tlp: Tlp = Tlp.AMBER
    pap: Pap = Pap.AMBER
    team: Key | None = None
    tasks: list[PlaybookTask] = []

    @model_validator(mode="after")
    def check_playbook(self) -> Self:
        seen: set[str] = set()
        for task in self.tasks:
            if task.key in seen:
                raise ValueError(f"template {self.key} has duplicate task {task.key}")
            if unknown := set(task.depends_on) - seen:
                raise ValueError(
                    f"task {task.key} can only depend on earlier tasks, not {sorted(unknown)}"
                )
            seen.add(task.key)
        return self


def _task(key: str, title: str, *depends_on: str, **extra: Any) -> PlaybookTask:
    return PlaybookTask(key=key, title=title, depends_on=list(depends_on), **extra)


CRITICAL = Condition(field="severity", value="critical")


DEFAULT_TEMPLATES = [
    Template(
        key="phishing",
        name="Phishing",
        impact="medium",
        urgency="high",
        tasks=[
            _task("recipients", "Identify every recipient and who clicked", due_hours=2),
            _task("block", "Block sender, URLs, and attachment hashes", due_hours=2),
            _task("reset", "Reset credentials of users who clicked", "recipients", due_hours=4),
            _task("purge", "Purge the message from all mailboxes", "recipients"),
        ],
    ),
    Template(
        key="ransomware",
        name="Ransomware",
        impact="high",
        urgency="high",
        tasks=[
            _task("isolate", "Isolate affected hosts", due_hours=1),
            _task("scope", "Identify the strain and scope of encryption", "isolate"),
            _task("backups", "Confirm backups are intact and offline", due_hours=4),
            _task("retainer", "Engage the IR retainer firm", when=CRITICAL, due_hours=2),
            _task("restore", "Restore from clean backups", "scope", "backups"),
        ],
    ),
    Template(
        key="bec",
        name="Business email compromise",
        impact="high",
        urgency="medium",
        tasks=[
            _task("payments", "Contact finance to stop or recall payments", due_hours=1),
            _task("mailbox", "Review mailbox rules and sign-in logs"),
            _task("reset", "Reset credentials and revoke sessions", "mailbox"),
        ],
    ),
    Template(
        key="insider",
        name="Insider threat",
        impact="high",
        urgency="medium",
        tlp=Tlp.RED,
        pap=Pap.RED,
        tasks=[
            _task("legal", "Engage legal and HR before any action", due_hours=4),
            _task("preserve", "Preserve evidence under legal hold", "legal"),
            _task("access", "Review and restrict the subject's access", "legal"),
        ],
    ),
    Template(
        key="ddos",
        name="DDoS",
        impact="medium",
        urgency="high",
        tasks=[
            _task("provider", "Engage the DDoS mitigation provider", due_hours=1),
            _task("monitor", "Monitor service availability until stable", "provider"),
        ],
    ),
    Template(
        key="data_exposure",
        name="Data exposure",
        impact="high",
        urgency="medium",
        tasks=[
            _task("contain", "Remove or restrict the exposed data", due_hours=4),
            _task("assess", "Assess what data and which people are affected", "contain"),
            _task("notify", "Decide on breach notification obligations", "assess", due_hours=48),
        ],
    ),
    Template(
        key="vulnerability_exploitation",
        name="Vulnerability exploitation",
        impact="high",
        urgency="high",
        tasks=[
            _task("patch", "Patch or mitigate the vulnerability", due_hours=24),
            _task("hunt", "Hunt for post-exploitation activity"),
        ],
    ),
]


class TenantConfig(BaseModel):
    states: list[State] = Field(min_length=2)
    custom_fields: list[CustomField]
    severity: Severity
    escalation_rules: list[EscalationRule]
    # Defaults let configurations saved before these sections existed still load.
    teams: list[Team] = []
    templates: list[Template] = Field(default=DEFAULT_TEMPLATES, min_length=1)

    @model_validator(mode="after")
    def check_references(self) -> Self:
        keys = self.state_keys
        _unique(keys, "state keys")
        if self.states[0].terminal:
            raise ValueError("the first state cannot be terminal")
        if not any(s.terminal for s in self.states):
            raise ValueError("at least one state must be terminal")
        _unique([f.key for f in self.custom_fields], "custom field keys")
        for field in self.custom_fields:
            if field.required_from is not None and field.required_from not in keys:
                raise ValueError(f"field {field.key} is required from unknown state")
        milestones = [s.sla_milestone for s in self.states if s.sla_milestone]
        _unique(milestones, "SLA milestones")
        if milestones == ["contain", "triage"]:
            raise ValueError("the triage milestone must come before the contain milestone")
        if any(s.terminal and s.sla_milestone for s in self.states):
            raise ValueError("terminal states already meet every SLA milestone")
        _unique([t.key for t in self.teams], "team keys")
        team_keys = {t.key for t in self.teams}
        for rule in self.escalation_rules:
            if unknown := set(rule.severities) - set(self.severity.keys):
                raise ValueError(f"rule {rule.name!r} uses unknown severities: {sorted(unknown)}")
            if rule.reassign_to_team is not None and rule.reassign_to_team not in team_keys:
                raise ValueError(f"rule {rule.name!r} reassigns to an unknown team")
        _unique([t.key for t in self.templates], "template keys")
        condition_fields = CONDITION_FIELDS | {f.key for f in self.custom_fields}
        for template in self.templates:
            if template.impact not in self.severity.impacts:
                raise ValueError(f"template {template.key} uses unknown impact")
            if template.urgency not in self.severity.urgencies:
                raise ValueError(f"template {template.key} uses unknown urgency")
            if template.team is not None and template.team not in team_keys:
                raise ValueError(f"template {template.key} uses unknown team")
            for task in template.tasks:
                if task.when is not None and task.when.field not in condition_fields:
                    raise ValueError(f"task {task.key} has a condition on an unknown field")
        return self

    def template(self, key: str) -> Template | None:
        return next((t for t in self.templates if t.key == key), None)

    def team(self, key: str | None) -> Team | None:
        return next((t for t in self.teams if t.key == key), None)

    @property
    def terminal_states(self) -> list[str]:
        return [s.key for s in self.states if s.terminal]

    @property
    def state_keys(self) -> list[str]:
        return [s.key for s in self.states]

    def validate_values(self, values: dict[str, Any], state: str) -> dict[str, str]:
        """Return an error per custom field key; empty when the values suit the case state."""
        fields = {f.key: f for f in self.custom_fields}
        errors = {key: "unknown field" for key in values if key not in fields}
        reached = self.state_keys.index(state)
        for field in fields.values():
            value = values.get(field.key)
            if value is None or value == "":
                if field.required_from and reached >= self.state_keys.index(field.required_from):
                    errors[field.key] = "required"
            elif not field.accepts(value):
                errors[field.key] = f"invalid {field.type}"
        return errors


def _sla(acknowledge: int, triage: int, contain: int, resolve: int) -> SlaTarget:
    return SlaTarget(
        acknowledge_minutes=acknowledge,
        triage_minutes=triage,
        contain_minutes=contain,
        resolve_minutes=resolve,
    )


HOUR, DAY = 60, 24 * 60

DEFAULT_CONFIG = TenantConfig(
    states=[
        State(key="new", name="New"),
        State(key="triage", name="Triage"),
        State(key="analysis", name="Analysis", sla_milestone="triage"),
        State(key="containment", name="Containment"),
        State(key="eradication", name="Eradication", sla_milestone="contain"),
        State(key="recovery", name="Recovery"),
        State(key="post_incident", name="Post-Incident"),
        State(key="closed", name="Closed", terminal=True, require_tasks_done=True),
    ],
    custom_fields=[],
    severity=Severity(
        levels=[
            SeverityLevel(key="critical", sla=_sla(15, HOUR, 4 * HOUR, DAY)),
            SeverityLevel(key="high", sla=_sla(30, 4 * HOUR, DAY, 3 * DAY)),
            SeverityLevel(key="medium", sla=_sla(4 * HOUR, DAY, 3 * DAY, 7 * DAY)),
            SeverityLevel(key="low", sla=_sla(DAY, 3 * DAY, 7 * DAY, 14 * DAY)),
        ],
        impacts=["high", "medium", "low"],
        urgencies=["high", "medium", "low"],
        matrix=[
            ["critical", "high", "medium"],
            ["high", "medium", "low"],
            ["medium", "low", "low"],
        ],
    ),
    escalation_rules=[],
)


async def load(db: AsyncSession, tenant_id: UUID) -> TenantConfig:
    data = await db.scalar(
        select(TenantConfigRow.data).where(TenantConfigRow.tenant_id == tenant_id)
    )
    return DEFAULT_CONFIG if data is None else TenantConfig.model_validate(data)


async def save(db: AsyncSession, tenant_id: UUID, config: TenantConfig, actor: str) -> None:
    values = {
        "data": config.model_dump(mode="json"),
        "updated_by": actor,
        "updated_at": func.now(),
    }
    await db.execute(
        insert(TenantConfigRow)
        .values(tenant_id=tenant_id, **values)
        .on_conflict_do_update(index_elements=["tenant_id"], set_=values)
    )
