"""Case tasks: dependencies, checklists, waivers, and tasks generated from playbooks."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit
from app.models import Case, Task
from app.tenant_config import CONDITION_FIELDS, MAX_TEXT, TenantConfig

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
LongText = Annotated[str, StringConstraints(max_length=MAX_TEXT)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class TaskStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    WAIVED = "waived"


FINISHED = {TaskStatus.DONE, TaskStatus.WAIVED}


class ChecklistItem(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    done: bool = False


class TaskIn(BaseModel):
    title: Title
    description: LongText = ""
    assignee_id: UUID | None = None
    due_at: AwareDatetime | None = None
    depends_on: list[UUID] = []
    checklist: list[ChecklistItem] = Field(default=[], max_length=50)


class TaskUpdate(BaseModel):
    """Fields to change; omitted fields stay as they are."""

    title: Title | None = None
    description: LongText | None = None
    assignee_id: UUID | None = None
    due_at: AwareDatetime | None = None
    status: TaskStatus | None = None
    waive_reason: Reason | None = None
    depends_on: list[UUID] | None = None
    checklist: list[ChecklistItem] | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def check_nulls(self) -> Self:
        nullable = {"assignee_id", "due_at", "waive_reason"}
        if nulls := sorted(k for k in self.model_fields_set - nullable if getattr(self, k) is None):
            raise ValueError(f"cannot be null: {', '.join(nulls)}")
        return self


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    description: str
    status: TaskStatus
    assignee_id: UUID | None
    due_at: datetime | None
    depends_on: list[UUID]
    checklist: list[ChecklistItem]
    waive_reason: str | None
    playbook_key: str | None
    blocked: bool = False
    created_at: datetime
    completed_at: datetime | None


def blocked(task: Task, by_id: dict[UUID, Task]) -> bool:
    return any(by_id[d].status not in FINISHED for d in task.depends_on if d in by_id)


def has_cycle(edges: dict[UUID, list[UUID]]) -> bool:
    """True if following depends_on edges ever returns to a task already on the path."""
    done: set[UUID] = set()

    def visit(node: UUID, path: set[UUID]) -> bool:
        if node in path:
            return True
        if node in done:
            return False
        found = any(visit(n, path | {node}) for n in edges.get(node, []))
        done.add(node)
        return found

    return any(visit(node, set()) for node in edges)


async def case_tasks(db: AsyncSession, case_id: UUID) -> list[Task]:
    query = select(Task).where(Task.case_id == case_id).order_by(Task.position, Task.created_at)
    return list((await db.scalars(query)).all())


def task_outs(tasks: Sequence[Task]) -> list[TaskOut]:
    by_id = {t.id: t for t in tasks}
    return [
        TaskOut.model_validate(t).model_copy(update={"blocked": blocked(t, by_id)}) for t in tasks
    ]


def _condition_met(case: Case, field: str, value: str) -> bool:
    actual = getattr(case, field) if field in CONDITION_FIELDS else case.custom.get(field)
    return actual is not None and str(actual) == value


async def sync_playbook(db: AsyncSession, case: Case, config: TenantConfig, actor: str) -> None:
    """Create the case's playbook tasks that are due: unconditional ones and newly matched ones."""
    template = config.template(case.category)
    if template is None:
        return
    existing = {t.playbook_key: t for t in await case_tasks(db, case.id) if t.playbook_key}
    now = datetime.now(UTC)
    for position, item in enumerate(template.tasks):
        if item.key in existing:
            continue
        if item.when is not None and not _condition_met(case, item.when.field, item.when.value):
            continue
        task = Task(
            tenant_id=case.tenant_id,
            case_id=case.id,
            title=item.title,
            description=item.description,
            status=TaskStatus.OPEN,
            due_at=now + timedelta(hours=item.due_hours) if item.due_hours else None,
            # Dependencies on conditional tasks that were never created are dropped.
            depends_on=[existing[key].id for key in item.depends_on if key in existing],
            checklist=[],
            playbook_key=item.key,
            position=position,
        )
        db.add(task)
        await db.flush()
        existing[item.key] = task
        await audit.record(
            db, actor, "task.create", f"case:{case.number}", case.tenant_id,
            {"task": str(task.id), "playbook": item.key},
        )  # fmt: skip


async def add_task(db: AsyncSession, case: Case, body: TaskIn) -> Task:
    last = await db.scalar(select(func.max(Task.position)).where(Task.case_id == case.id))
    task = Task(
        tenant_id=case.tenant_id,
        case_id=case.id,
        title=body.title,
        description=body.description,
        status=TaskStatus.OPEN,
        assignee_id=body.assignee_id,
        due_at=body.due_at,
        depends_on=body.depends_on,
        checklist=[item.model_dump() for item in body.checklist],
        position=(last if last is not None else -1) + 1,
    )
    db.add(task)
    await db.flush()
    return task


def apply_update(task: Task, change: TaskUpdate, by_id: dict[UUID, Task]) -> str | None:
    """Apply a change to a task; return why it is not allowed, or None."""
    fields = change.model_fields_set
    for key in fields - {"status", "checklist"}:
        setattr(task, key, getattr(change, key))
    if change.checklist is not None:
        task.checklist = [item.model_dump() for item in change.checklist]
    if change.depends_on is not None:
        if task.id in change.depends_on or not set(change.depends_on) <= by_id.keys():
            return "dependencies must be other tasks of this case"
        if has_cycle({t.id: list(t.depends_on) for t in by_id.values()}):
            return "dependencies would form a cycle"
    if change.status is not None and change.status != task.status:
        if change.status in (TaskStatus.IN_PROGRESS, TaskStatus.DONE) and blocked(task, by_id):
            return "the task is blocked by unfinished dependencies"
        task.status = change.status
        task.completed_at = datetime.now(UTC) if change.status in FINISHED else None
    if task.status == TaskStatus.WAIVED and not task.waive_reason:
        return "waiving a task needs a reason"
    if task.status != TaskStatus.WAIVED:
        task.waive_reason = None
    return None
