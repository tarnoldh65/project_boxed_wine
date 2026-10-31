"""Fire SLA warning and breach escalations, then deliver pending notifications."""

from datetime import UTC, datetime

from sqlalchemy import select

from app import cases, escalation, tenant_config
from app.db import SessionLocal, set_tenant
from app.models import Case, Tenant
from app.tenant_config import Trigger

TRIGGERS = {"warning": Trigger.SLA_WARNING, "breached": Trigger.SLA_BREACH}


async def check_tenant(tenant: Tenant) -> None:
    async with SessionLocal() as db:
        await set_tenant(db, tenant.id)
        config = await tenant_config.load(db, tenant.id)
        now = datetime.now(UTC)
        open_cases = await db.scalars(select(Case).where(Case.state.not_in(config.terminal_states)))
        for case in open_cases:
            for clock in cases.clocks(case, config, now):
                if trigger := TRIGGERS.get(clock.status):
                    await escalation.fire(db, config, case, trigger, clock.step)
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
