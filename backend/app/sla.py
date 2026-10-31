"""SLA clocks: deadlines run in calendar time from when the case was opened."""

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from app.tenant_config import SlaTarget

Step = Literal["acknowledge", "triage", "contain", "resolve"]
Status = Literal["met", "late", "pending", "warning", "breached"]
STEPS: tuple[Step, ...] = ("acknowledge", "triage", "contain", "resolve")
# A clock warns once this share of its target has elapsed.
WARNING_FRACTION = 0.75
SEVERITY_ORDER: tuple[Status, ...] = ("breached", "warning", "late", "pending", "met")


class SlaClock(BaseModel):
    step: Step
    due_at: datetime
    met_at: datetime | None
    status: Status


def clocks(
    target: SlaTarget, opened: datetime, met: dict[Step, datetime | None], now: datetime
) -> list[SlaClock]:
    result = []
    for step in STEPS:
        minutes = getattr(target, f"{step}_minutes")
        due = opened + timedelta(minutes=minutes)
        met_at = met[step]
        status: Status
        if met_at is not None:
            status = "met" if met_at <= due else "late"
        elif now > due:
            status = "breached"
        elif now >= opened + timedelta(minutes=minutes * WARNING_FRACTION):
            status = "warning"
        else:
            status = "pending"
        result.append(SlaClock(step=step, due_at=due, met_at=met_at, status=status))
    return result


def worst(statuses: list[SlaClock]) -> Status | None:
    present = {c.status for c in statuses}
    return next((s for s in SEVERITY_ORDER if s in present), None)
