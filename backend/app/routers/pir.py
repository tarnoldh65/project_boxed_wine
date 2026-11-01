from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException

from app import cases, pir, tenant_config
from app.auth import TenantContext, require
from app.cases import CaseOut
from app.db import Db
from app.models import Case
from app.pir import ActionIn, ActionUpdate, PirIn

router = APIRouter(prefix="/api/tenants/{slug}/cases/{number}/pir", tags=["pir"])

Writer = Annotated[TenantContext, require("cases:write")]


async def _out(db: Db, ctx: TenantContext, case: Case) -> CaseOut:
    return await cases.case_out(db, ctx, case, await tenant_config.load(db, ctx.tenant.id))


@router.put("")
async def save_pir(number: int, body: PirIn, ctx: Writer, db: Db) -> CaseOut:
    case = await cases.get_case(db, ctx, number)
    await pir.save(db, case, ctx.principal.actor, body)
    await ctx.audit(db, "pir.update", f"case:{number}", **body.model_dump())
    return await _out(db, ctx, case)


@router.post("/actions", status_code=201)
async def add_action(number: int, body: ActionIn, ctx: Writer, db: Db) -> CaseOut:
    case = await cases.get_case(db, ctx, number)
    if body.owner_id is not None and body.owner_id not in {
        m.id for m in await cases.members(db, ctx.tenant.id)
    }:
        cases.invalid("owner must be a tenant member", "owner_id")
    action = await pir.add_action(db, case, ctx.principal.actor, body)
    await ctx.audit(
        db, "pir.action_create", f"case:{number}", item=str(action.id), title=action.title
    )
    return await _out(db, ctx, case)


@router.patch("/actions/{action_id}")
async def update_action(
    number: int, action_id: UUID, body: ActionUpdate, ctx: Writer, db: Db
) -> CaseOut:
    case = await cases.get_case(db, ctx, number)
    action = await pir.get_action(db, case, action_id)
    if action is None:
        raise HTTPException(404, "Action item not found")
    if body.done != (action.done_at is not None):
        action.done_at = datetime.now(UTC) if body.done else None
        await ctx.audit(
            db, "pir.action_update", f"case:{number}", item=str(action.id), done=body.done
        )
    return await _out(db, ctx, case)
