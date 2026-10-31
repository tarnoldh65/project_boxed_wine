from email.message import EmailMessage
from typing import Any, ClassVar

import pytest
from pytest import MonkeyPatch
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import settings
from app.jobs import email_intake


def message(subject: str = "Invoice overdue", msg_id: str = "<m1@example.test>") -> bytes:
    msg = EmailMessage()
    msg["From"] = "Billing <billing@evil.example>"
    msg["To"] = "phish@example.test"
    msg["Subject"] = subject
    msg["Message-ID"] = msg_id
    msg["Date"] = "Fri, 30 Oct 2026 10:00:00 +0000"
    msg.set_content("Pay now: hxxp://evil[.]example/pay")
    msg.add_attachment(b"MZ...", maintype="application", subtype="octet-stream", filename="a.exe")
    return msg.as_bytes()


def test_parse_message_keeps_headers_body_and_attachment_names() -> None:
    alert = email_intake.parse_message(message(), "phishing")

    assert (alert.title, alert.source, alert.template) == ("Invoice overdue", "email", "phishing")
    assert alert.external_id == "<m1@example.test>"
    assert alert.payload["from"] == "Billing <billing@evil.example>"
    assert alert.payload["body"].strip() == "Pay now: hxxp://evil[.]example/pay"
    assert alert.payload["attachments"] == [
        {"filename": "a.exe", "content_type": "application/octet-stream"}
    ]


def test_parse_message_without_subject_or_plain_body() -> None:
    msg = EmailMessage()
    msg.set_content("<p>Hello</p>", subtype="html")
    alert = email_intake.parse_message(msg.as_bytes(), "phishing")
    assert alert.title == "(no subject)"
    assert alert.external_id is None
    assert "<p>Hello</p>" in alert.payload["body"]


class FakeImap:
    """Just enough of imaplib.IMAP4 for the job: an inbox of unread messages."""

    instances: ClassVar[list[FakeImap]] = []
    inbox: ClassVar[dict[bytes, bytes]] = {}

    def __init__(self, host: str, port: int) -> None:
        self.address = (host, port)
        self.seen: list[bytes] = []
        FakeImap.instances.append(self)

    def login(self, user: str, password: str) -> None:
        self.credentials = (user, password)

    def select(self, mailbox: str) -> None:
        self.mailbox = mailbox

    def search(self, charset: None, criterion: str) -> tuple[str, list[bytes]]:
        return "OK", [b" ".join(n for n in self.inbox if n not in self.seen)]

    def fetch(self, number: bytes, parts: str) -> tuple[str, list[Any]]:
        return "OK", [(b"envelope", self.inbox[number])]

    def store(self, number: bytes, command: str, flags: str) -> None:
        self.seen.append(number)

    def logout(self) -> None:
        self.closed = True


@pytest.fixture
def mailbox(monkeypatch: MonkeyPatch, tenant: str) -> type[FakeImap]:
    FakeImap.instances = []
    FakeImap.inbox = {b"1": message(), b"2": message("Second", "<m2@example.test>")}
    monkeypatch.setattr(email_intake.imaplib, "IMAP4", FakeImap)
    monkeypatch.setattr(
        settings, "imap_url", "imap://intake%40example.test:p%40ss@mail:3143/Alerts"
    )
    monkeypatch.setattr(settings, "imap_tenant", tenant)
    return FakeImap


async def alerts_in(owner: AsyncEngine, tenant: str) -> list[str]:
    async with owner.connect() as conn:
        rows = await conn.scalars(
            text(
                "SELECT a.title FROM alerts a JOIN tenants t ON t.id = a.tenant_id "
                "WHERE t.slug = :slug ORDER BY a.title"
            ),
            {"slug": tenant},
        )
        return list(rows)


async def test_job_turns_unread_mail_into_cases(
    mailbox: type[FakeImap], owner: AsyncEngine, tenant: str
) -> None:
    assert await email_intake.run() == 0

    imap = mailbox.instances[0]
    assert imap.address == ("mail", 3143)
    assert imap.credentials == ("intake@example.test", "p@ss")
    assert imap.mailbox == "Alerts"
    assert imap.seen == [b"1", b"2"]
    assert imap.closed
    assert await alerts_in(owner, tenant) == ["Invoice overdue", "Second"]


async def test_job_skips_messages_already_stored(
    mailbox: type[FakeImap], owner: AsyncEngine, tenant: str
) -> None:
    await email_intake.run()
    # The mailbox lost its read flags (e.g. restored); Message-IDs prevent duplicates.
    assert await email_intake.run() == 0
    assert await alerts_in(owner, tenant) == ["Invoice overdue", "Second"]


async def test_job_requires_configuration(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "imap_url", None)
    assert await email_intake.run() == 1
