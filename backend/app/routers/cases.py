from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from app import cases, tenant_config
from app.auth import TenantContext, require
from app.cases import (
    AssetIn,
    AssetOut,
    CaseCreate,
    CaseOut,
    CaseSummary,
    CaseUpdate,
    MergeIn,
    SplitIn,
)
from app.db import Db
from app.models import Asset, Case
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
    await ctx.audit(db, "case.list", "cases", state=state, count=len(found))
    return [CaseSummary.model_validate(c) for c in found]


@router.post("/cases", status_code=201)
async def create_case(body: CaseCreate, ctx: Writer, db: Db) -> CaseOut:
    config = await tenant_config.load(db, ctx.tenant.id)
    case = await cases.create_case(db, ctx.tenant.id, config, ctx.principal.actor, body)
    return await cases.case_out(db, case)


@router.get("/cases/{number}")
async def get_case(number: int, ctx: Reader, db: Db) -> CaseOut:
    case = await cases.get_case(db, ctx, number)
    await ctx.audit(db, "case.view", f"case:{number}")
    return await cases.case_out(db, case)


@router.patch("/cases/{number}")
async def update_case(number: int, body: CaseUpdate, ctx: Writer, db: Db) -> CaseOut:
    case = await cases.get_case(db, ctx, number)
    config = await tenant_config.load(db, ctx.tenant.id)
    await cases.update_case(db, ctx, config, case, body)
    return await cases.case_out(db, case)


@router.post("/cases/{number}/merge")
async def merge_case(number: int, body: MergeIn, ctx: Writer, db: Db) -> CaseOut:
    source = await cases.get_case(db, ctx, number)
    target = await cases.get_case(db, ctx, body.into)
    config = await tenant_config.load(db, ctx.tenant.id)
    await cases.merge(db, ctx, config, source, target)
    return await cases.case_out(db, target)


@router.post("/cases/{number}/split", status_code=201)
async def split_case(number: int, body: SplitIn, ctx: Writer, db: Db) -> CaseOut:
    source = await cases.get_case(db, ctx, number)
    config = await tenant_config.load(db, ctx.tenant.id)
    new = await cases.split(db, ctx, config, source, body)
    return await cases.case_out(db, new)


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
