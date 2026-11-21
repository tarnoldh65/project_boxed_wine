from typing import Annotated

from fastapi import APIRouter

from app import cases, outbound, reports, tenant_config
from app.auth import TenantContext, require
from app.cases import CaseOut
from app.db import Db
from app.reports import Kind
from app.routers.reports import context

router = APIRouter(prefix="/api/tenants/{slug}/cases/{number}", tags=["outbound"])

Writer = Annotated[TenantContext, require("cases:write")]


@router.post("/jira")
async def create_jira_issue(number: int, ctx: Writer, db: Db) -> CaseOut:
    case = await cases.get_case(db, ctx, number)
    config = await tenant_config.load(db, ctx.tenant.id)
    await outbound.create_issue(db, ctx, config, case)
    return await cases.case_out(db, ctx, case, config)


@router.post("/reports/{kind}/confluence")
async def publish_report(number: int, kind: Kind, ctx: Writer, db: Db) -> CaseOut:
    case = await cases.get_case(db, ctx, number)
    config = await tenant_config.load(db, ctx.tenant.id)
    report = reports.build(kind, await context(db, ctx, case, config))
    await outbound.publish(db, ctx, config, case, kind, report)
    return await cases.case_out(db, ctx, case, config)
