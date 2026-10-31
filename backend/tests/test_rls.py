import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import SessionLocal, set_tenant
from app.models import Tenant
from app.rls import tenant_rls

# Tables with a tenant_id that are deliberately not row-level secured: identity and
# audit data the application must read before (or regardless of) choosing a tenant.
GLOBAL_TABLES = {"memberships", "api_tokens", "audit_log"}


async def test_app_role_cannot_bypass_rls(owner: AsyncEngine) -> None:
    async with owner.connect() as conn:
        role = (
            await conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'casemgr_app'")
            )
        ).one()
        owned = await conn.scalar(
            text("SELECT count(*) FROM pg_tables WHERE tableowner = 'casemgr_app'")
        )
    assert tuple(role) == (False, False)
    assert owned == 0


async def test_every_tenant_owned_table_is_isolated(owner: AsyncEngine) -> None:
    async with owner.connect() as conn:
        rows = await conn.execute(
            text(
                """
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                       EXISTS (SELECT 1 FROM pg_policy p
                               WHERE p.polrelid = c.oid AND p.polname = 'tenant_isolation')
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
                JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id'
                WHERE c.relkind = 'r'
                """
            )
        )
        tables = {name: (rls, force, policy) for name, rls, force, policy in rows}
    assert tables.keys() >= GLOBAL_TABLES
    for name, secured in tables.items():
        if name not in GLOBAL_TABLES:
            assert secured == (True, True, True), name


async def test_rls_policy_isolates_tenants(owner: AsyncEngine) -> None:
    async with owner.begin() as conn:
        await conn.execute(
            text("CREATE TABLE rls_probe (tenant_id uuid NOT NULL, label text NOT NULL)")
        )
        for statement in tenant_rls("rls_probe"):
            await conn.execute(text(statement))
        await conn.execute(text("GRANT SELECT, INSERT ON rls_probe TO casemgr_app"))
        await conn.execute(text("INSERT INTO rls_probe SELECT id, slug FROM tenants"))
    try:
        async with SessionLocal() as db:
            labels = text("SELECT label FROM rls_probe")
            assert (await db.scalars(labels)).all() == []

            tenants = dict((await db.execute(select(Tenant.slug, Tenant.id))).all())
            await set_tenant(db, tenants["detect-respond"])
            assert (await db.scalars(labels)).all() == ["detect-respond"]

            with pytest.raises(DBAPIError, match="row-level security"):
                await db.execute(
                    text("INSERT INTO rls_probe VALUES (:tenant, 'smuggled')"),
                    {"tenant": tenants["insider-threat"]},
                )
    finally:
        async with owner.begin() as conn:
            await conn.execute(text("DROP TABLE rls_probe"))
