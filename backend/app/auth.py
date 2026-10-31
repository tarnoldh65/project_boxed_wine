import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, NoReturn
from uuid import UUID

from authlib.integrations.base_client import OAuthError
from fastapi import Depends, HTTPException, Request
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit, idp
from app.config import settings
from app.db import Db, set_tenant
from app.models import ApiToken, Membership, Tenant, User, UserSession

SESSION_COOKIE = "casemgr_session"
GROUP_PREFIX = "casemgr-"
PLATFORM_ADMIN_GROUP = "casemgr-platform_admin"

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": frozenset({"cases:read", "cases:write", "tokens:manage"}),
    "ir_lead": frozenset({"cases:read", "cases:write"}),
    "analyst": frozenset({"cases:read", "cases:write"}),
    "legal": frozenset({"cases:read"}),
    "executive": frozenset({"cases:read"}),
    "auditor": frozenset({"cases:read"}),
}
# Flags are granted on top of a role and act as permissions themselves.
FLAGS = frozenset({"restricted_case", "malware:read"})


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def permissions_for(grants: Iterable[str]) -> frozenset[str]:
    return frozenset().union(*(ROLE_PERMISSIONS.get(g, {g}) for g in grants))


def parse_groups(groups: Iterable[str], tenants: Mapping[str, UUID]) -> set[tuple[UUID, str]]:
    """Map IdP groups named casemgr-<tenant>-<grant> to (tenant_id, grant) pairs."""
    grants: set[tuple[UUID, str]] = set()
    for group in groups:
        if not group.startswith(GROUP_PREFIX):
            continue
        slug, _, grant = group.removeprefix(GROUP_PREFIX).rpartition("-")
        if slug in tenants and (grant in ROLE_PERMISSIONS or grant in FLAGS):
            grants.add((tenants[slug], grant))
    return grants


async def sync_grants(db: AsyncSession, user: User, groups: list[str]) -> None:
    """Replace the user's memberships with those implied by their current IdP groups."""
    tenants = dict((await db.execute(select(Tenant.slug, Tenant.id))).all())
    new = parse_groups(groups, tenants)
    old = set(
        await db.execute(
            select(Membership.tenant_id, Membership.grant_name).where(Membership.user_id == user.id)
        )
    )
    platform_admin = PLATFORM_ADMIN_GROUP in groups
    if new == old and platform_admin == user.is_platform_admin:
        return
    user.is_platform_admin = platform_admin
    await db.execute(delete(Membership).where(Membership.user_id == user.id))
    db.add_all(Membership(user_id=user.id, tenant_id=t, grant_name=g) for t, g in new)
    await audit.record(
        db,
        f"user:{user.id}",
        "grants.synced",
        f"user:{user.id}",
        detail={
            "platform_admin": platform_admin,
            "grants": sorted(f"{t}:{g}" for t, g in new),
        },
    )


@dataclass(frozen=True)
class Principal:
    actor: str
    user_id: UUID | None
    is_platform_admin: bool
    permissions: Mapping[UUID, frozenset[str]]


def unauthorized() -> NoReturn:
    raise HTTPException(401, "Not authenticated")


async def _end_session(db: AsyncSession, session: UserSession) -> NoReturn:
    await db.delete(session)
    await db.commit()
    unauthorized()


async def _token_principal(db: AsyncSession, raw: str) -> Principal:
    token = await db.scalar(
        select(ApiToken).where(
            ApiToken.token_hash == hash_secret(raw),
            ApiToken.revoked_at.is_(None),
            ApiToken.expires_at > func.now(),
        )
    )
    if token is None:
        unauthorized()
    return Principal(
        actor=f"token:{token.id}",
        user_id=None,
        is_platform_admin=False,
        permissions={token.tenant_id: frozenset(token.scopes)},
    )


async def _session_principal(db: AsyncSession, raw: str) -> Principal:
    session = await db.scalar(select(UserSession).where(UserSession.token_hash == hash_secret(raw)))
    if session is None:
        unauthorized()
    now = datetime.now(UTC)
    if session.last_seen_at < now - timedelta(
        minutes=settings.session_idle_minutes
    ) or session.created_at < now - timedelta(hours=settings.session_max_hours):
        await _end_session(db, session)
    user = await db.get_one(User, session.user_id)
    if session.synced_at <= now - timedelta(minutes=settings.group_sync_minutes):
        try:
            groups, session.refresh_token = await idp.refresh(session.refresh_token)
        except OAuthError:
            await _end_session(db, session)
        await sync_grants(db, user, groups)
        session.synced_at = now
    session.last_seen_at = now
    await db.flush()
    rows = await db.execute(
        select(Membership.tenant_id, Membership.grant_name).where(Membership.user_id == user.id)
    )
    grants: dict[UUID, list[str]] = {}
    for tenant_id, grant in rows:
        grants.setdefault(tenant_id, []).append(grant)
    return Principal(
        actor=f"user:{user.id}",
        user_id=user.id,
        is_platform_admin=user.is_platform_admin,
        permissions={t: permissions_for(g) for t, g in grants.items()},
    )


async def current_principal(request: Request, db: Db) -> Principal:
    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        return await _token_principal(db, authorization.removeprefix("Bearer "))
    if raw := request.cookies.get(SESSION_COOKIE):
        return await _session_principal(db, raw)
    unauthorized()


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


async def deny(
    db: AsyncSession, principal: Principal, target: str, tenant_id: UUID | None = None
) -> NoReturn:
    await audit.record(db, principal.actor, "access.denied", target, tenant_id)
    await db.commit()
    raise HTTPException(403, "Forbidden")


async def current_user(principal: CurrentPrincipal, db: Db) -> Principal:
    if principal.user_id is None:
        await deny(db, principal, "user-only")
    return principal


async def platform_admin(principal: CurrentPrincipal, db: Db) -> Principal:
    if not principal.is_platform_admin:
        await deny(db, principal, "platform-admin")
    return principal


@dataclass(frozen=True)
class TenantContext:
    tenant: Tenant
    principal: Principal
    permissions: frozenset[str]

    async def audit(self, db: AsyncSession, action: str, target: str, **detail: Any) -> None:
        await audit.record(db, self.principal.actor, action, target, self.tenant.id, detail)


async def tenant_context(slug: str, principal: CurrentPrincipal, db: Db) -> TenantContext:
    """Resolve the tenant in the path and confine the transaction to it."""
    tenant = await db.scalar(select(Tenant).where(Tenant.slug == slug))
    if tenant is None or tenant.id not in principal.permissions:
        await deny(db, principal, f"tenant:{slug}")
    await set_tenant(db, tenant.id)
    return TenantContext(tenant, principal, principal.permissions[tenant.id])


def require(permission: str) -> Any:
    async def dependency(
        ctx: Annotated[TenantContext, Depends(tenant_context)], db: Db
    ) -> TenantContext:
        if permission not in ctx.permissions:
            await deny(db, ctx.principal, f"permission:{permission}", ctx.tenant.id)
        return ctx

    return Depends(dependency)
