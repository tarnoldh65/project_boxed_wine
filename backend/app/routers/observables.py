from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app import cases, observable_store, tenant_config
from app.auth import TenantContext, require
from app.cases import CaseOut
from app.db import Db
from app.models import Alert
from app.observable_store import CandidateOut, ExtractIn, LinkIn, ObservablePage
from app.observables import defang, extract, normalize, strings_in

router = APIRouter(prefix="/api/tenants/{slug}", tags=["observables"])

Reader = Annotated[TenantContext, require("cases:read")]
Writer = Annotated[TenantContext, require("cases:write")]


@router.post("/cases/{number}/observables/extract")
async def extract_observables(
    number: int, body: ExtractIn, ctx: Writer, db: Db
) -> list[CandidateOut]:
    """Propose observables from pasted text, or from the case's alerts when no text is given."""
    case = await cases.get_case(db, ctx, number)
    if body.text is None:
        alerts = await db.scalars(select(Alert).where(Alert.case_id == case.id))
        text = "\n".join(line for a in alerts for line in (a.title, *strings_in(a.payload)))
    else:
        text = body.text
    linked = await observable_store.linked_values(db, case)
    await ctx.audit(db, "observable.extract", f"case:{number}", from_alerts=body.text is None)
    return [
        CandidateOut(
            type=c.type,
            value=c.value,
            defanged=defang(c.type, c.value),
            linked=(c.type, c.value) in linked,
        )
        for c in extract(text)
    ]


@router.post("/cases/{number}/observables", status_code=201)
async def link_observables(number: int, body: LinkIn, ctx: Writer, db: Db) -> CaseOut:
    case = await cases.get_case(db, ctx, number)
    items: list[tuple[str, str]] = []
    for i, item in enumerate(body.observables):
        try:
            items.append((item.type, normalize(item.type, item.value)))
        except ValueError as error:
            cases.invalid(str(error), "observables", i, "value")
    ids = await observable_store.link(db, case, list(dict.fromkeys(items)))
    await ctx.audit(db, "observable.link", f"case:{number}", observables=sorted(map(str, ids)))
    return await cases.case_out(db, ctx, case, await tenant_config.load(db, ctx.tenant.id))


@router.delete("/cases/{number}/observables/{observable_id}", status_code=204)
async def unlink_observable(number: int, observable_id: UUID, ctx: Writer, db: Db) -> None:
    case = await cases.get_case(db, ctx, number)
    if not await observable_store.unlink(db, case, observable_id):
        raise HTTPException(404, "Observable not linked to this case")
    await ctx.audit(db, "observable.unlink", f"case:{number}", observable=str(observable_id))


@router.get("/observables/{observable_id}")
async def get_observable(observable_id: UUID, ctx: Reader, db: Db) -> ObservablePage:
    found = await observable_store.page(db, observable_id, "restricted_case" in ctx.permissions)
    if found is None:
        raise HTTPException(404, "Observable not found")
    await ctx.audit(db, "observable.view", f"observable:{observable_id}")
    return found
