"""Purge closed cases past their type's retention period; cases under legal hold are kept."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app import audit, storage, tenant_config
from app.db import SessionLocal, set_tenant
from app.models import Case, Evidence, Tenant

ACTOR = "system:retention"


async def purge_tenant(tenant: Tenant) -> int:
    """Purge the tenant's expired cases, one transaction each; returns how many."""
    async with SessionLocal() as db:
        await set_tenant(db, tenant.id)
        config = await tenant_config.load(db, tenant.id)
        now = datetime.now(UTC)
        expired = []
        for template in config.templates:
            if template.retention_days is None:
                continue
            rows = await db.execute(
                select(Case.id, Case.number, Case.closed_at).where(
                    Case.category == template.key,
                    Case.closed_at < now - timedelta(days=template.retention_days),
                    Case.legal_hold.is_(False),
                )
            )
            expired += [(template, *row) for row in rows]
        for template, case_id, number, closed_at in expired:
            stored = await db.scalars(
                select(Evidence.object_key).where(Evidence.case_id == case_id)
            )
            keys = [key for key in stored if key]
            await db.execute(select(func.purge_case(case_id)))
            await audit.record(
                db,
                ACTOR,
                "case.purge",
                f"case:{number}",
                tenant.id,
                {
                    "category": template.key,
                    "retention_days": template.retention_days,
                    "closed_at": closed_at.isoformat(),
                    "evidence_objects": len(keys),
                },
            )
            await db.commit()
            for key in keys:
                storage.delete(key)
    return len(expired)


async def run() -> int:
    async with SessionLocal() as db:
        tenants = (await db.scalars(select(Tenant).order_by(Tenant.slug))).all()
    for tenant in tenants:
        if purged := await purge_tenant(tenant):
            print(f"{tenant.slug}: {purged} cases purged")
    return 0
