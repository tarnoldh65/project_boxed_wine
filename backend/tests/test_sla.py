from datetime import UTC, datetime, timedelta

import pytest

from app import sla
from app.tenant_config import SlaTarget

OPENED = datetime(2026, 10, 31, 23, 50, tzinfo=UTC)  # deadlines cross midnight and month end
TARGET = SlaTarget(
    acknowledge_minutes=20, triage_minutes=60, contain_minutes=240, resolve_minutes=1440
)
NOTHING: dict[sla.Step, datetime | None] = dict.fromkeys(sla.STEPS)


def at(minutes: float) -> datetime:
    return OPENED + timedelta(minutes=minutes)


def acknowledge(now: datetime, met_at: datetime | None = None) -> sla.SlaClock:
    met: dict[sla.Step, datetime | None] = {**NOTHING, "acknowledge": met_at}
    return sla.clocks(TARGET, OPENED, met, now)[0]


def test_deadlines_follow_the_target() -> None:
    due = [c.due_at for c in sla.clocks(TARGET, OPENED, NOTHING, OPENED)]
    assert due == [
        datetime(2026, 11, 1, 0, 10, tzinfo=UTC),
        datetime(2026, 11, 1, 0, 50, tzinfo=UTC),
        datetime(2026, 11, 1, 3, 50, tzinfo=UTC),
        datetime(2026, 11, 1, 23, 50, tzinfo=UTC),
    ]


@pytest.mark.parametrize(
    ("minutes", "status"),
    [
        (0, "pending"),
        (14.99, "pending"),
        (15, "warning"),  # 75% of 20 minutes
        (20, "warning"),  # due now, not yet late
        (20.001, "breached"),
        (600, "breached"),
    ],
)
def test_unmet_clock_status(minutes: float, status: str) -> None:
    assert acknowledge(at(minutes)).status == status


@pytest.mark.parametrize(
    ("met_minutes", "status"),
    [(5, "met"), (20, "met"), (21, "late")],
)
def test_met_clock_status(met_minutes: float, status: str) -> None:
    # Once met, the clock no longer depends on the current time.
    assert acknowledge(at(10_000), met_at=at(met_minutes)).status == status


def test_worst_status() -> None:
    met: dict[sla.Step, datetime | None] = {**NOTHING, "acknowledge": at(30)}
    clocks = sla.clocks(TARGET, OPENED, met, at(50))
    assert [c.status for c in clocks] == ["late", "warning", "pending", "pending"]
    assert sla.worst(clocks) == "warning"
    assert sla.worst([]) is None
