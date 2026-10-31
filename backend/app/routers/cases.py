from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

from app import cases, tasks, tenant_config
from app.auth import TenantContext, require
from app.cases import (
    AssetIn,
    AssetOut,
    CaseCreate,
    CaseOut,
    CaseSummary,
    CaseUpdate,
    MajorIncidentIn,
    MergeIn,
    SplitIn,
)
from app.db import Db
from app.models import Asset, Case
from app.tasks import TaskIn, TaskUpdate
from app.tenant_config import Key

router = APIRouter(prefix="/api/tenants/{slug}", tags=["cases"])

Reader = Annotated[TenantContext, require("cases:read")]
Writer = Annotated[TenantContext, require("cases:write")]


class MemberOut(BaseModel):
    id: UUID
    username: str
    display_name: str


@router.get("/members")
async def list_members(ctx: Reader, db: Db) -> list[MemberOut]:
    return [
        MemberOut(id=m.id, username=m.username, display_name=m.display_name)
        for m in await cases.members(db, ctx.tenant.id)
    ]


@router.get("/cases")
async def list_cases(
    ctx: Reader,
    db: Db,
    state: Key | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[CaseSummary]:
    query = cases.visible(ctx).order_by(Case.created_at.desc()).limit(limit)
    if state is not None:
        query = query.where(Case.state == state)
    found = (await db.scalars(query)).all()
    config = await tenant_config.load(db, ctx.tenant.id)
    await ctx.audit(db, "case.list", "cases", state=state, count=len(found))
    now = datetime.now(UTC)
    return [cases.summary(c, config, now) for c in found]


@router.post("/cases", status_code=201)
async def create_case(body: CaseCreate, ctx: Writer, db: Db) -> CaseOut:
    config = await tenant_config.load(db, ctx.tenant.id)
    case = await cases.create_case(db, ctx.tenant.id, config, ctx.principal.actor, body)
    return await cases.case_out(db, case, config)


@router.get("/cases/{number}")
async def get_case(number: int, ctx: Reader, db: Db) -> CaseOut:
    case = await cases.get_case(db, ctx, number)
    await ctx.audit(db, "case.view", f"case:{number}")
    return await cases.case_out(db, case, await tenant_config.load(db, ctx.tenant.id))


@router.patch("/cases/{number}")
async def update_case(number: int, body: CaseUpdate, ctx: Writer, db: Db) -> CaseOut:
    case = await cases.get_case(db, ctx, number)
    config = await tenant_config.load(db, ctx.tenant.id)
    await cases.update_case(db, ctx, config, case, body)
    return await cases.case_out(db, case, config)


@router.post("/cases/{number}/acknowledge")
async def acknowledge_case(number: int, ctx: Writer, db: Db) -> CaseOut:
    case = await cases.get_case(db, ctx, number)
    await cases.acknowledge(db, ctx, case)
    return await cases.case_out(db, case, await tenant_config.load(db, ctx.tenant.id))


@router.post("/cases/{number}/major-incident")
async def declare_major_incident(
    number: int, body: MajorIncidentIn, ctx: Writer, db: Db
) -> CaseOut:
    case = await cases.get_case(db, ctx, number)
    config = await tenant_config.load(db, ctx.tenant.id)
    await cases.declare_major_incident(db, ctx, config, case, body.reason)
    return await cases.case_out(db, case, config)


@router.post("/cases/{number}/merge")
async def merge_case(number: int, body: MergeIn, ctx: Writer, db: Db) -> CaseOut:
    source = await cases.get_case(db, ctx, number)
    target = await cases.get_case(db, ctx, body.into)
    config = await tenant_config.load(db, ctx.tenant.id)
    await cases.merge(db, ctx, config, source, target)
    return await cases.case_out(db, target, config)


@router.post("/cases/{number}/split", status_code=201)
async def split_case(number: int, body: SplitIn, ctx: Writer, db: Db) -> CaseOut:
    source = await cases.get_case(db, ctx, number)
    config = await tenant_config.load(db, ctx.tenant.id)
    new = await cases.split(db, ctx, config, source, body)
    return await cases.case_out(db, new, config)


async def _open_case(db: Db, ctx: TenantContext, number: int) -> tuple[Case, set[UUID]]:
    """A visible, open case for task changes, and the ids of tenant members."""
    case = await cases.get_case(db, ctx, number)
    config = await tenant_config.load(db, ctx.tenant.id)
    if case.state in config.terminal_states:
        cases.invalid("tasks of a closed case cannot change")
    return case, {m.id for m in await cases.members(db, ctx.tenant.id)}


@router.post("/cases/{number}/tasks", status_code=201)
async def add_task(number: int, body: TaskIn, ctx: Writer, db: Db) -> CaseOut:
    case, member_ids = await _open_case(db, ctx, number)
    if body.assignee_id is not None and body.assignee_id not in member_ids:
        cases.invalid("assignee must be a tenant member", "assignee_id")
    own = {t.id for t in await tasks.case_tasks(db, case.id)}
    if not set(body.depends_on) <= own:
        cases.invalid("dependencies must be other tasks of this case", "depends_on")
    task = await tasks.add_task(db, case, body)
    await ctx.audit(db, "task.create", f"case:{number}", task=str(task.id))
    return await cases.case_out(db, case, await tenant_config.load(db, ctx.tenant.id))


@router.patch("/cases/{number}/tasks/{task_id}")
async def update_task(number: int, task_id: UUID, body: TaskUpdate, ctx: Writer, db: Db) -> CaseOut:
    case, member_ids = await _open_case(db, ctx, number)
    by_id = {t.id: t for t in await tasks.case_tasks(db, case.id)}
    task = by_id.get(task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    if body.assignee_id is not None and body.assignee_id not in member_ids:
        cases.invalid("assignee must be a tenant member", "assignee_id")
    if problem := tasks.apply_update(task, body, by_id):
        cases.invalid(problem)
    await ctx.audit(
        db,
        "task.update",
        f"case:{number}",
        task=str(task.id),
        changes=body.model_dump(mode="json", exclude_unset=True),
    )
    return await cases.case_out(db, case, await tenant_config.load(db, ctx.tenant.id))


@router.post("/cases/{number}/assets", status_code=201)
async def link_asset(number: int, body: AssetIn, ctx: Writer, db: Db) -> AssetOut:
    case = await cases.get_case(db, ctx, number)
    return AssetOut.model_validate(await cases.link_asset(db, ctx, case, body))


@router.delete("/cases/{number}/assets/{asset_id}", status_code=204)
async def unlink_asset(number: int, asset_id: UUID, ctx: Writer, db: Db) -> None:
    case = await cases.get_case(db, ctx, number)
    await cases.unlink_asset(db, ctx, case, asset_id)


@router.get("/assets")
async def search_assets(
    ctx: Reader, db: Db, q: Annotated[str, Query(max_length=200)] = ""
) -> list[AssetOut]:
    pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    found = await db.scalars(
        select(Asset).where(Asset.name.ilike(pattern)).order_by(Asset.kind, Asset.name).limit(50)
    )
    await ctx.audit(db, "asset.search", "assets", q=q)
    return [AssetOut.model_validate(a) for a in found]
