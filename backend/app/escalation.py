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

from app import audit, connections
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
        slack = list(rule.slack)
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
                slack=slack,
                # A reassignment-only rule has nothing to deliver.
                sent_at=None if emails or webhook or slack else datetime.now(UTC),
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
    if trigger == "mention":
        event = "you were mentioned in a note"
    elif trigger in (Trigger.CLOCK_WARNING, Trigger.CLOCK_BREACH):
        due = "approaching" if trigger == Trigger.CLOCK_WARNING else "missed"
        event = f"{step} notification deadline {due}"
    elif step:
        event = f"{step} SLA {trigger.removeprefix('sla_')}"
    else:
        event = trigger.replace("_", " ")
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


def slack_message(content: dict[str, Any], title: str | None) -> dict[str, Any]:
    """A Slack message: the case, severity, and event, plus the title unless it is restricted."""

    def escape(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    case = f"Case #{content['case_number']}"
    heading = f"{case}: {title}" if title else case
    event = content["summary"].split(": ", 1)[1]
    return {
        "text": f"{heading} ({content['severity']}): {event} {content['url']}",
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*<{content['url']}|{escape(heading)}>*"},
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"*{escape(content['severity'])}*"},
                    {"type": "mrkdwn", "text": escape(event)},
                    {"type": "mrkdwn", "text": escape(content["tenant"])},
                ],
            },
        ],
    }


def post_slack(tenant: str, name: str, message: dict[str, Any]) -> None:
    """Post to one of the tenant's Slack connections. Errors never include the webhook URL."""
    connection = connections.available(tenant, "slack").get(name)
    if not isinstance(connection, connections.SlackConnection):
        raise RuntimeError(f"Slack connection {name!r} is not available to this tenant")
    try:
        response = httpx2.post(connection.webhook_url.get_secret_value(), json=message, timeout=10)
    except httpx2.HTTPError as error:
        raise RuntimeError(f"Slack connection {name!r} failed: {type(error).__name__}") from None
    if response.status_code >= 400:
        raise RuntimeError(f"Slack connection {name!r} answered HTTP {response.status_code}")


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
            if row.slack:
                message = slack_message(content, None if case.restricted else case.title)
                for name in row.slack:
                    post_slack(tenant.slug, name, message)
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
