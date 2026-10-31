from uuid import UUID, uuid4

import pytest

from app.models import Task
from app.tasks import TaskStatus, TaskUpdate, apply_update, blocked, has_cycle

A, B, C, D = (UUID(int=i) for i in range(1, 5))


@pytest.mark.parametrize(
    ("edges", "cycle"),
    [
        ({}, False),
        ({A: []}, False),
        ({A: [B], B: [C]}, False),
        ({A: [B, C], B: [C], C: []}, False),  # diamond
        ({A: [A]}, True),
        ({A: [B], B: [A]}, True),
        ({A: [B], B: [C], C: [A]}, True),
        ({D: [A], A: [B], B: [C], C: [B]}, True),
    ],
)
def test_has_cycle(edges: dict[UUID, list[UUID]], cycle: bool) -> None:
    assert has_cycle(edges) is cycle


def task(id_: UUID, status: TaskStatus = TaskStatus.OPEN, *depends_on: UUID) -> Task:
    return Task(id=id_, status=status, depends_on=list(depends_on), waive_reason=None, checklist=[])


def test_blocked_until_dependencies_finish() -> None:
    first, second = task(A), task(B, TaskStatus.OPEN, A)
    by_id = {A: first, B: second}
    assert blocked(second, by_id)
    first.status = TaskStatus.WAIVED
    assert not blocked(second, by_id)
    assert not blocked(task(C, TaskStatus.OPEN, uuid4()), by_id)  # unknown ids are ignored


def update(target: Task, by_id: dict[UUID, Task], **change: object) -> str | None:
    return apply_update(target, TaskUpdate.model_validate(change), by_id)


def test_status_rules() -> None:
    first, second = task(A), task(B, TaskStatus.OPEN, A)
    by_id = {A: first, B: second}

    assert update(second, by_id, status="done") == "the task is blocked by unfinished dependencies"
    assert update(first, by_id, status="waived") == "waiving a task needs a reason"
    assert update(first, by_id, status="waived", waive_reason="not applicable") is None
    assert first.completed_at is not None
    assert update(second, by_id, status="in_progress") is None
    assert update(second, by_id, status="done") is None

    assert update(first, by_id, status="open") is None
    assert (first.waive_reason, first.completed_at) == (None, None)


def test_dependency_rules() -> None:
    first, second = task(A), task(B, TaskStatus.OPEN, A)
    by_id = {A: first, B: second}
    assert update(first, by_id, depends_on=[A]) == "dependencies must be other tasks of this case"
    assert update(first, by_id, depends_on=[C]) == "dependencies must be other tasks of this case"
    assert update(first, by_id, depends_on=[B]) == "dependencies would form a cycle"


def test_non_nullable_fields() -> None:
    with pytest.raises(ValueError, match="cannot be null: status, title"):
        TaskUpdate.model_validate({"title": None, "status": None, "due_at": None})
