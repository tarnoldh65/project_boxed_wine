from typing import Annotated, Any

from fastapi import APIRouter
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app import cases, connections, outbound, tenant_config
from app.auth import TenantContext, require
from app.connections import ConnectionOut
from app.db import Db
from app.tenant_config import (
    ConfluenceSettings,
    CustomField,
    EscalationRule,
    JiraSettings,
    Regime,
    Severity,
    State,
    Team,
    Template,
    TenantConfig,
)

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


@router.get("/connections")
async def get_connections(ctx: Manager) -> list[ConnectionOut]:
    """The outside services this tenant may use, by name. Credentials never leave the server."""
    found = connections.available(ctx.tenant.slug)
    return [ConnectionOut(name=name, type=c.type) for name, c in found.items()]


@router.put("/escalation-rules")
async def put_escalation_rules(body: list[EscalationRule], ctx: Manager, db: Db) -> TenantConfig:
    slack = connections.available(ctx.tenant.slug, "slack")
    for i, rule in enumerate(body):
        for j, name in enumerate(rule.slack):
            if name not in slack:
                cases.invalid("not a Slack connection this tenant may use", i, "slack", j)
    return await _replace(db, ctx, "escalation_rules", body)


@router.put("/teams")
async def put_teams(body: list[Team], ctx: Manager, db: Db) -> TenantConfig:
    member_ids = {m.id for m in await cases.members(db, ctx.tenant.id)}
    for i, team in enumerate(body):
        if team.on_call is not None and team.on_call not in member_ids:
            cases.invalid("not a tenant member", i, "on_call")
    return await _replace(db, ctx, "teams", body)


@router.put("/templates")
async def put_templates(body: list[Template], ctx: Manager, db: Db) -> TenantConfig:
    return await _replace(db, ctx, "templates", body)


@router.put("/jira")
async def put_jira(ctx: Manager, db: Db, body: JiraSettings | None = None) -> TenantConfig:
    if problem := outbound.setup_problem(ctx.tenant.slug, "jira", body):
        cases.invalid(problem, "connection")
    return await _replace(db, ctx, "jira", body)


@router.put("/confluence")
async def put_confluence(
    ctx: Manager, db: Db, body: ConfluenceSettings | None = None
) -> TenantConfig:
    if problem := outbound.setup_problem(ctx.tenant.slug, "confluence", body):
        cases.invalid(problem, "connection")
    return await _replace(db, ctx, "confluence", body)


@router.put("/regimes")
async def put_regimes(body: list[Regime], ctx: Manager, db: Db) -> TenantConfig:
    return await _replace(db, ctx, "regimes", body)
