from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter
from sqlalchemy import select

from app import cases, regulatory, tenant_config
from app.auth import TenantContext, require
from app.cases import CaseOut
from app.db import Db
from app.models import Case, RegulatoryClock
from app.regulatory import ActiveClockOut, DecisionIn, DisclosureIn

router = APIRouter(prefix="/api/tenants/{slug}", tags=["regulatory"])

Reader = Annotated[TenantContext, require("cases:read")]
Writer = Annotated[TenantContext, require("cases:write")]
Legal = Annotated[TenantContext, require("legal:manage")]


@router.get("/clocks")
async def active_clocks(ctx: Reader, db: Db) -> list[ActiveClockOut]:
    """Unmet notification deadlines of the cases the caller can see, soonest first."""
    rows = await db.execute(
        select(RegulatoryClock, Case.number)
        .join(Case, Case.id == RegulatoryClock.case_id)
        .where(
            RegulatoryClock.met_at.is_(None),
            Case.merged_into_id.is_(None),
            Case.id.in_(cases.visible(ctx).with_only_columns(Case.id)),
        )
        .order_by(RegulatoryClock.due_at)
    )
    now = datetime.now(UTC)
    found = [
        ActiveClockOut(**regulatory.clock_out(clock, now).model_dump(), case_number=number)
        for clock, number in rows
    ]
    await ctx.audit(db, "clock.list", "clocks", count=len(found))
    return found


@router.post("/cases/{number}/materiality", status_code=201)
async def decide(number: int, body: DecisionIn, ctx: Legal, db: Db) -> CaseOut:
    case = await cases.get_case(db, ctx, number)
    config = await tenant_config.load(db, ctx.tenant.id)
    decision, started = await regulatory.decide(db, config, case, ctx.principal.actor, body)
    await ctx.audit(
        db,
        "materiality.decide",
        f"case:{number}",
        decision=str(decision.id),
        **body.model_dump(mode="json"),
        clocks_started=started,
    )
    return await cases.case_out(db, ctx, case, config)


@router.post("/cases/{number}/disclosures", status_code=201)
async def disclose(number: int, body: DisclosureIn, ctx: Writer, db: Db) -> CaseOut:
    case = await cases.get_case(db, ctx, number)
    if problem := await regulatory.disclosure_problem(db, case, body):
        cases.invalid(problem)
    disclosure = await regulatory.disclose(db, case, ctx.principal.actor, body)
    await ctx.audit(
        db,
        "disclosure.create",
        f"case:{number}",
        disclosure=str(disclosure.id),
        **body.model_dump(mode="json"),
    )
    return await cases.case_out(db, ctx, case, await tenant_config.load(db, ctx.tenant.id))
