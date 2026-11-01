"""Fire warning and breach escalations for breach-notification clocks, then deliver them."""

from datetime import UTC, datetime

from sqlalchemy import select

from app import escalation, tenant_config
from app.db import SessionLocal, set_tenant
from app.models import Case, RegulatoryClock, Tenant
from app.tenant_config import Trigger


async def check_tenant(tenant: Tenant) -> None:
    async with SessionLocal() as db:
        await set_tenant(db, tenant.id)
        config = await tenant_config.load(db, tenant.id)
        now = datetime.now(UTC)
        # Clocks keep running after a case closes; a merged case's clocks moved with it.
        due = await db.execute(
            select(RegulatoryClock, Case)
            .join(Case, Case.id == RegulatoryClock.case_id)
            .where(
                RegulatoryClock.met_at.is_(None),
                RegulatoryClock.warn_at <= now,
                Case.merged_into_id.is_(None),
            )
        )
        for clock, case in due:
            trigger = Trigger.CLOCK_BREACH if now > clock.due_at else Trigger.CLOCK_WARNING
            await escalation.fire(db, config, case, trigger, clock.regime)
        await db.commit()


async def run() -> int:
    async with SessionLocal() as db:
        tenants = (await db.scalars(select(Tenant).order_by(Tenant.slug))).all()
    for tenant in tenants:
        await check_tenant(tenant)
        sent, failed = await escalation.deliver(tenant)
        if sent or failed:
            print(f"{tenant.slug}: {sent} notifications sent, {failed} failed")
    return 0
