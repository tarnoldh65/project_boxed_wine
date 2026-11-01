from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import ColumnElement, delete, select

from app import cases, search
from app.auth import TenantContext, require
from app.db import Db
from app.models import SavedSearch
from app.search import Hit, SavedSearchIn, SavedSearchOut

router = APIRouter(prefix="/api/tenants/{slug}", tags=["search"])

Reader = Annotated[TenantContext, require("cases:read")]


@router.get("/search")
async def run_search(
    ctx: Reader,
    db: Db,
    q: Annotated[str, Query(min_length=1, max_length=500)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[Hit]:
    found = await search.run(db, ctx, q.strip(), limit)
    await ctx.audit(db, "search.run", "search", query=q, count=len(found))
    return found


def _mine(ctx: TenantContext) -> ColumnElement[bool]:
    return SavedSearch.user_id == ctx.principal.user_id


@router.get("/searches")
async def list_saved(ctx: Reader, db: Db) -> list[SavedSearchOut]:
    found = await db.scalars(select(SavedSearch).where(_mine(ctx)).order_by(SavedSearch.name))
    return [SavedSearchOut.model_validate(s) for s in found]


@router.post("/searches", status_code=201)
async def save_search(body: SavedSearchIn, ctx: Reader, db: Db) -> SavedSearchOut:
    if ctx.principal.user_id is None:
        raise HTTPException(403, "Only people can save searches")
    if await db.scalar(select(SavedSearch.id).where(_mine(ctx), SavedSearch.name == body.name)):
        cases.invalid("a saved search with this name already exists", "name")
    saved = SavedSearch(
        tenant_id=ctx.tenant.id, user_id=ctx.principal.user_id, name=body.name, query=body.query
    )
    db.add(saved)
    await db.flush()
    return SavedSearchOut.model_validate(saved)


@router.delete("/searches/{search_id}", status_code=204)
async def delete_saved(search_id: UUID, ctx: Reader, db: Db) -> None:
    removed = await db.scalar(
        delete(SavedSearch).where(_mine(ctx), SavedSearch.id == search_id).returning(SavedSearch.id)
    )
    if removed is None:
        raise HTTPException(404, "Saved search not found")
