from typing import Annotated, Any

from fastapi import APIRouter, Path, Response

from app import cases, tenant_config
from app.auth import TenantContext, require
from app.cases import AlertIn, IntakeOut
from app.db import Db
from app.tenant_config import Key

router = APIRouter(prefix="/api/tenants/{slug}", tags=["intake"])

Ingester = Annotated[TenantContext, require("alerts:write")]


async def _ingest(db: Db, ctx: TenantContext, alert: AlertIn, response: Response) -> IntakeOut:
    config = await tenant_config.load(db, ctx.tenant.id)
    result = await cases.ingest(db, ctx.tenant.id, config, ctx.principal.actor, alert)
    response.status_code = 200 if result.duplicate else 201
    return result


@router.post("/alerts", status_code=201)
async def post_alert(body: AlertIn, ctx: Ingester, db: Db, response: Response) -> IntakeOut:
    return await _ingest(db, ctx, body, response)


@router.post("/webhooks/{source}", status_code=201)
async def post_webhook(
    source: Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9_.-]{0,39}$")],
    body: dict[str, Any],
    ctx: Ingester,
    db: Db,
    response: Response,
    template: Key,
    title_field: str = "title",
    id_field: str | None = None,
    group_field: str | None = None,
) -> IntakeOut:
    """Accept any JSON object; query parameters name the top-level keys to map."""

    def field(name: str | None) -> str | None:
        value = body.get(name) if name else None
        return None if value is None or value == "" else str(value)[:200]

    alert = AlertIn(
        template=template,
        title=(field(title_field) or f"{source} alert")[:300],
        source=source,
        external_id=field(id_field),
        group_key=field(group_field),
        payload=body,
    )
    return await _ingest(db, ctx, alert, response)
