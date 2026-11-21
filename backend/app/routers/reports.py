from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Response

from app import cases, reports, tenant_config
from app.auth import TenantContext, require
from app.db import Db
from app.evidence import content_disposition
from app.models import Case
from app.reports import Format, Kind
from app.tenant_config import TenantConfig

router = APIRouter(prefix="/api/tenants/{slug}/cases/{number}", tags=["reports"])

Reader = Annotated[TenantContext, require("cases:read")]
MEDIA = {
    media: {"schema": {"type": "string", "format": "binary"}}
    for media in reports.MEDIA_TYPES.values()
}


async def context(db: Db, ctx: TenantContext, case: Case, config: TenantConfig) -> reports.Context:
    return reports.Context(
        case=await cases.case_out(db, ctx, case, config),
        config=config,
        tenant=ctx.tenant.name,
        members={m.id: m.display_name for m in await cases.members(db, ctx.tenant.id)},
        generated_at=datetime.now(UTC),
    )


@router.get("/reports/{kind}", response_class=Response, responses={200: {"content": MEDIA}})
async def report(
    number: int, kind: Kind, ctx: Reader, db: Db, format: Format = Format.PDF
) -> Response:
    case = await cases.get_case(db, ctx, number)
    config = await tenant_config.load(db, ctx.tenant.id)
    built = reports.build(kind, await context(db, ctx, case, config))
    body = reports.render(built, format)
    await ctx.audit(db, "report.generate", f"case:{number}", kind=kind, format=format)
    return Response(
        body,
        media_type=reports.MEDIA_TYPES[format],
        headers={"Content-Disposition": content_disposition(f"case-{number}-{kind}.{format}")},
    )
