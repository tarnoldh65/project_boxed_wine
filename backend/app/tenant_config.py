"""Per-tenant configuration: lifecycle, custom fields, severity and SLAs, escalation rules."""

from datetime import date
from enum import StrEnum
from math import isfinite
from typing import Annotated, Any, Self
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

    @model_validator(mode="after")
    def check_action(self) -> Self:
        if not self.notify_emails and self.webhook_url is None:
            raise ValueError(f"rule {self.name!r} needs an email or a webhook")
        if self.webhook_url is not None and self.webhook_url.scheme != "https":
            raise ValueError(f"rule {self.name!r} webhook must use https")
        return self


class TenantConfig(BaseModel):
    states: list[State] = Field(min_length=2)
    custom_fields: list[CustomField]
    severity: Severity
    escalation_rules: list[EscalationRule]

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
        for rule in self.escalation_rules:
            if unknown := set(rule.severities) - set(self.severity.keys):
                raise ValueError(f"rule {rule.name!r} uses unknown severities: {sorted(unknown)}")
        return self

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
        State(key="analysis", name="Analysis"),
        State(key="containment", name="Containment"),
        State(key="eradication", name="Eradication"),
        State(key="recovery", name="Recovery"),
        State(key="post_incident", name="Post-Incident"),
        State(key="closed", name="Closed", terminal=True),
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
