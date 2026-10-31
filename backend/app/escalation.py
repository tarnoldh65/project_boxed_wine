"""Escalation rules: firing them into the notifications outbox and delivering it."""

import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any
from uuid import UUID

import httpx2
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit
from app.config import settings
from app.db import SessionLocal, set_tenant
from app.models import Case, Notification, Tenant
from app.tenant_config import TenantConfig, Trigger

ACTOR = "system:escalation"
MAX_ATTEMPTS = 5


async def fire(
    db: AsyncSession, config: TenantConfig, case: Case, trigger: Trigger, step: str = ""
) -> None:
    """Fire every matching rule once per case, trigger, and SLA step."""
    for rule in config.escalation_rules:
        if rule.trigger != trigger or (rule.severities and case.severity not in rule.severities):
            continue
        emails = [str(e) for e in rule.notify_emails]
        webhook = str(rule.webhook_url) if rule.webhook_url else None
        fired = await db.scalar(
            insert(Notification)
            .values(
                tenant_id=case.tenant_id,
                case_id=case.id,
                rule=rule.name,
                trigger=trigger,
                step=step,
                emails=emails,
                webhook=webhook,
                # A reassignment-only rule has nothing to deliver.
                sent_at=None if emails or webhook else datetime.now(UTC),
            )
            .on_conflict_do_nothing(index_elements=["case_id", "rule", "trigger", "step"])
            .returning(Notification.id)
        )
        if fired is None:
            continue
        team = config.team(rule.reassign_to_team)
        if team is not None:
            case.team = team.key
            case.owner_id = team.on_call or case.owner_id
        detail = {"rule": rule.name, "trigger": trigger, "step": step}
        if team is not None:
            detail["reassigned_to"] = team.key
        await audit.record(
            db, ACTOR, "escalation.fire", f"case:{case.number}", case.tenant_id, detail
        )


def describe(slug: str, number: int, severity: str, trigger: str, step: str) -> dict[str, Any]:
    """Notification content. Case titles and details stay in the system; only a link leaves it."""
    event = f"{step} SLA {trigger.removeprefix('sla_')}" if step else trigger.replace("_", " ")
    return {
        "tenant": slug,
        "case_number": number,
        "severity": severity,
        "trigger": trigger,
        "step": step or None,
        "summary": f"Case #{number} ({severity}): {event}",
        "url": f"{settings.public_url}/t/{slug}/cases/{number}",
    }


def send_email(to: list[str], content: dict[str, Any]) -> None:
    if settings.smtp_host is None:
        raise RuntimeError("SMTP is not configured (CASEMGR_SMTP_HOST)")
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = ", ".join(to)
    message["Subject"] = f"[{content['tenant']}] {content['summary']}"
    message.set_content(f"{content['summary']}\n\n{content['url']}\n")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password or "")
        smtp.send_message(message)


def post_webhook(url: str, content: dict[str, Any]) -> None:
    httpx2.post(url, json=content, timeout=10).raise_for_status()


async def _deliver_one(tenant: Tenant, notification_id: UUID) -> bool:
    async with SessionLocal() as db:
        await set_tenant(db, tenant.id)
        row = await db.get_one(Notification, notification_id, with_for_update={"skip_locked": True})
        case = await db.get_one(Case, row.case_id)
        content = describe(tenant.slug, case.number, case.severity, row.trigger, row.step)
        try:
            if row.emails:
                send_email(row.emails, content)
            if row.webhook:
                post_webhook(row.webhook, content)
        except Exception as error:  # any delivery failure is retried later
            row.attempts += 1
            row.last_error = str(error)[:500]
        else:
            row.sent_at = datetime.now(UTC)
        await db.commit()
        return row.sent_at is not None


async def deliver(tenant: Tenant) -> tuple[int, int]:
    """Send the tenant's pending notifications; return (sent, failed)."""
    async with SessionLocal() as db:
        await set_tenant(db, tenant.id)
        pending = (
            await db.scalars(
                select(Notification.id).where(
                    Notification.sent_at.is_(None), Notification.attempts < MAX_ATTEMPTS
                )
            )
        ).all()
    results = [await _deliver_one(tenant, n) for n in pending]
    return results.count(True), results.count(False)
