from typing import Annotated, Any

from fastapi import APIRouter
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app import tenant_config
from app.auth import TenantContext, require
from app.db import Db
from app.tenant_config import CustomField, EscalationRule, Severity, State, TenantConfig

router = APIRouter(prefix="/api/tenants/{slug}/config", tags=["config"])

Reader = Annotated[TenantContext, require("cases:read")]
Manager = Annotated[TenantContext, require("config:manage")]


@router.get("")
async def get_config(ctx: Reader, db: Db) -> TenantConfig:
    return await tenant_config.load(db, ctx.tenant.id)


async def _replace(db: Db, ctx: TenantContext, section: str, value: Any) -> TenantConfig:
    """Replace one section, revalidating the whole configuration before saving."""
    current = await tenant_config.load(db, ctx.tenant.id)
    try:
        config = TenantConfig.model_validate(current.model_dump() | {section: value})
    except ValidationError as e:
        raise RequestValidationError(e.errors(include_url=False, include_context=False)) from e
    await tenant_config.save(db, ctx.tenant.id, config, ctx.principal.actor)
    await ctx.audit(
        db, "config.update", f"config:{section}", data=config.model_dump(mode="json")[section]
    )
    return config


@router.put("/states")
async def put_states(body: list[State], ctx: Manager, db: Db) -> TenantConfig:
    return await _replace(db, ctx, "states", body)


@router.put("/custom-fields")
async def put_custom_fields(body: list[CustomField], ctx: Manager, db: Db) -> TenantConfig:
    return await _replace(db, ctx, "custom_fields", body)


@router.put("/severity")
async def put_severity(body: Severity, ctx: Manager, db: Db) -> TenantConfig:
    return await _replace(db, ctx, "severity", body)


@router.put("/escalation-rules")
async def put_escalation_rules(body: list[EscalationRule], ctx: Manager, db: Db) -> TenantConfig:
    return await _replace(db, ctx, "escalation_rules", body)
