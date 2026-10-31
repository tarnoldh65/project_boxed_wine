"""Poll an IMAP mailbox and turn each unread message into an alert."""

import email
import imaplib
from email import policy
from email.message import EmailMessage
from urllib.parse import unquote, urlparse

from sqlalchemy import select

from app import cases, tenant_config
from app.cases import AlertIn
from app.config import settings
from app.db import SessionLocal, set_tenant
from app.models import Tenant
from app.tenant_config import MAX_TEXT

ACTOR = "system:email_intake"


def parse_message(raw: bytes, template: str) -> AlertIn:
    message = email.message_from_bytes(raw, policy=policy.default)
    assert isinstance(message, EmailMessage)  # noqa: S101 - guaranteed by policy.default
    body = message.get_body(preferencelist=("plain", "html"))
    attachments = [
        {"filename": part.get_filename(), "content_type": part.get_content_type()}
        for part in message.iter_attachments()
    ]
    subject = str(message.get("Subject", "")).strip()
    return AlertIn(
        template=template,
        title=subject[:300] or "(no subject)",
        source="email",
        external_id=message.get("Message-ID"),
        payload={
            "from": str(message.get("From", "")),
            "to": str(message.get("To", "")),
            "subject": subject,
            "date": str(message.get("Date", "")),
            "message_id": message.get("Message-ID"),
            "body": body.get_content()[:MAX_TEXT] if body else "",
            "attachments": attachments,
        },
    )


def _connect(imap_url: str) -> imaplib.IMAP4:
    url = urlparse(imap_url)
    imap_class = imaplib.IMAP4_SSL if url.scheme == "imaps" else imaplib.IMAP4
    imap = imap_class(url.hostname or "", url.port or (993 if url.scheme == "imaps" else 143))
    imap.login(unquote(url.username or ""), unquote(url.password or ""))
    imap.select(url.path.lstrip("/") or "INBOX")
    return imap


async def run() -> int:
    if not (settings.imap_url and settings.imap_tenant):
        print("email intake is not configured (CASEMGR_IMAP_URL, CASEMGR_IMAP_TENANT)")
        return 1
    imap = _connect(settings.imap_url)
    try:
        _, found = imap.search(None, "UNSEEN")
        for number in found[0].split():
            _, data = imap.fetch(number, "(BODY.PEEK[])")
            fetched = data[0]
            assert isinstance(fetched, tuple)  # noqa: S101 - FETCH returns (envelope, body)
            alert = parse_message(fetched[1], settings.imap_template)
            async with SessionLocal() as db:
                tenant = await db.scalar(select(Tenant).where(Tenant.slug == settings.imap_tenant))
                assert tenant is not None, f"unknown tenant {settings.imap_tenant}"  # noqa: S101
                await set_tenant(db, tenant.id)
                config = await tenant_config.load(db, tenant.id)
                result = await cases.ingest(db, tenant.id, config, ACTOR, alert)
                await db.commit()
            # Mark read only once the alert is stored; re-reads are de-duplicated by Message-ID.
            imap.store(number, "+FLAGS", "\\Seen")
            print(f"message {number.decode()} -> case {result.case_number}")
    finally:
        imap.logout()
    return 0
