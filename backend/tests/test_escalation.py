from typing import Any

import pytest
from httpx2 import AsyncClient
from pytest import MonkeyPatch
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app import escalation
from app.config import settings
from app.jobs import sla as sla_job
from app.models import Tenant
from tests.conftest import MakeUser, SignedIn

RULES = [
    {
        "name": "Breach page",
        "trigger": "sla_breach",
        "severities": ["high"],
        "notify_emails": ["soc@example.com"],
        "webhook_url": "https://hooks.example.com/page",
    },
    {"name": "Warn", "trigger": "sla_warning", "notify_emails": ["leads@example.com"]},
    {"name": "Escalate", "trigger": "sla_breach", "reassign_to_team": "tier2"},
    {"name": "Major", "trigger": "major_incident", "notify_emails": ["ciso@example.com"]},
    {"name": "New critical", "trigger": "case_created", "severities": ["critical"],
     "notify_emails": ["soc@example.com"]},
]  # fmt: skip


@pytest.fixture
async def team_lead(make_user: MakeUser, tenant: str) -> SignedIn:
    return await make_user({tenant: ["ir_lead"]})


@pytest.fixture
async def analyst(make_user: MakeUser, tenant: str, team_lead: SignedIn) -> SignedIn:
    admin = await make_user({tenant: ["admin"]})
    config = f"/api/tenants/{tenant}/config"
    teams = [{"key": "tier2", "name": "Tier 2", "on_call": str(team_lead.id)}]
    for section, body in (("teams", teams), ("escalation-rules", RULES)):
        response = await admin.client.put(f"{config}/{section}", json=body)
        assert response.status_code == 200, response.text
    return await make_user({tenant: ["analyst"]})


@pytest.fixture
def outbox(monkeypatch: MonkeyPatch) -> list[tuple[str, Any, dict[str, Any]]]:
    sent: list[tuple[str, Any, dict[str, Any]]] = []
    monkeypatch.setattr(escalation, "send_email", lambda to, c: sent.append(("email", to, c)))
    monkeypatch.setattr(escalation, "post_webhook", lambda u, c: sent.append(("webhook", u, c)))
    return sent


async def create(client: AsyncClient, tenant: str, **body: Any) -> dict[str, Any]:
    response = await client.post(
        f"/api/tenants/{tenant}/cases",
        json={"template": "phishing", "title": "Secret title"} | body,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def age(owner: AsyncEngine, tenant: str, number: int, minutes: int) -> None:
    async with owner.begin() as conn:
        await conn.execute(
            text(
                "UPDATE cases SET created_at = now() - make_interval(mins => :m) "
                "FROM tenants t WHERE cases.tenant_id = t.id AND t.slug = :slug AND number = :n"
            ),
            {"m": minutes, "slug": tenant, "n": number},
        )


async def notifications(owner: AsyncEngine, tenant: str) -> list[tuple[Any, ...]]:
    async with owner.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT n.rule, n.trigger, n.step, n.sent_at IS NOT NULL, n.attempts "
                "FROM notifications n JOIN tenants t ON t.id = n.tenant_id "
                "WHERE t.slug = :slug ORDER BY n.rule, n.step"
            ),
            {"slug": tenant},
        )
        return [tuple(r) for r in rows]


async def test_breaches_escalate_once(
    analyst: SignedIn,
    team_lead: SignedIn,
    tenant: str,
    owner: AsyncEngine,
    outbox: list[tuple[str, Any, dict[str, Any]]],
) -> None:
    number = (await create(analyst.client, tenant))["number"]
    # High severity: acknowledge 30 min, triage 4 h. At 3.5 h triage warns; acknowledge breached.
    await age(owner, tenant, number, 210)
    tenant_ = await tenant_row(owner, tenant)

    for _ in range(2):  # a second pass fires nothing new
        await sla_job.check_tenant(tenant_)
        await escalation.deliver(tenant_)

    assert await notifications(owner, tenant) == [
        ("Breach page", "sla_breach", "acknowledge", True, 0),
        ("Escalate", "sla_breach", "acknowledge", True, 0),
        ("Warn", "sla_warning", "triage", True, 0),
    ]
    assert [(kind, target) for kind, target, _ in outbox] == [
        ("email", ["soc@example.com"]),
        ("webhook", "https://hooks.example.com/page"),
        ("email", ["leads@example.com"]),
    ]
    content = outbox[0][2]
    assert content["summary"] == f"Case #{number} (high): acknowledge SLA breach"
    assert content["url"] == f"http://localhost:8080/t/{tenant}/cases/{number}"
    assert "Secret title" not in str(outbox)

    case = (await analyst.client.get(f"/api/tenants/{tenant}/cases/{number}")).json()
    assert (case["team"], case["owner_id"]) == ("tier2", str(team_lead.id))


async def test_creation_and_major_incident_triggers(
    analyst: SignedIn,
    tenant: str,
    owner: AsyncEngine,
    outbox: list[tuple[str, Any, dict[str, Any]]],
) -> None:
    await create(analyst.client, tenant)  # high: no "New critical" rule match
    critical = await create(analyst.client, tenant, template="ransomware")
    await analyst.client.post(
        f"/api/tenants/{tenant}/cases/{critical['number']}/major-incident",
        json={"reason": "Encryption spreading"},
    )

    await escalation.deliver(await tenant_row(owner, tenant))

    assert await notifications(owner, tenant) == [
        ("Major", "major_incident", "", True, 0),
        ("New critical", "case_created", "", True, 0),
    ]
    assert {c["summary"] for _, _, c in outbox} == {
        f"Case #{critical['number']} (critical): major incident",
        f"Case #{critical['number']} (critical): case created",
    }


async def tenant_row(owner: AsyncEngine, slug: str) -> Tenant:
    async with owner.connect() as conn:
        query = text("SELECT id, slug FROM tenants WHERE slug = :s")
        row = (await conn.execute(query, {"s": slug})).one()
    return Tenant(id=row.id, slug=row.slug)


async def test_job_runs_every_tenant(outbox: list[tuple[str, Any, dict[str, Any]]]) -> None:
    assert await sla_job.run() == 0


async def test_failed_delivery_is_retried(
    analyst: SignedIn, tenant: str, owner: AsyncEngine, monkeypatch: MonkeyPatch
) -> None:
    await create(analyst.client, tenant, template="ransomware")

    def broken(to: list[str], content: dict[str, Any]) -> None:
        raise ConnectionError("mail relay unreachable")

    monkeypatch.setattr(escalation, "send_email", broken)
    tenant_ = await tenant_row(owner, tenant)
    assert await escalation.deliver(tenant_) == (0, 1)
    assert await notifications(owner, tenant) == [("New critical", "case_created", "", False, 1)]

    monkeypatch.setattr(escalation, "send_email", lambda to, content: None)
    assert await escalation.deliver(tenant_) == (1, 0)
    assert await escalation.deliver(tenant_) == (0, 0)


async def test_delivery_gives_up_after_max_attempts(
    analyst: SignedIn, tenant: str, owner: AsyncEngine, monkeypatch: MonkeyPatch
) -> None:
    await create(analyst.client, tenant, template="ransomware")
    monkeypatch.setattr(escalation, "send_email", lambda to, content: 1 / 0)
    tenant_ = await tenant_row(owner, tenant)
    for _ in range(escalation.MAX_ATTEMPTS):
        await escalation.deliver(tenant_)
    assert await escalation.deliver(tenant_) == (0, 0)
    async with owner.connect() as conn:
        error = await conn.scalar(
            text("SELECT last_error FROM notifications WHERE tenant_id = :t"), {"t": tenant_.id}
        )
    assert error == "division by zero"


class FakeSmtp:
    sent: list[Any] = []  # noqa: RUF012

    def __init__(self, host: str, port: int, timeout: int) -> None:
        self.calls: list[tuple[Any, ...]] = [("connect", host, port)]

    def __enter__(self) -> FakeSmtp:
        return self

    def __exit__(self, *exc: object) -> None:
        FakeSmtp.sent.append(self.calls)

    def starttls(self) -> None:
        self.calls.append(("starttls",))

    def login(self, user: str, password: str) -> None:
        self.calls.append(("login", user, password))

    def send_message(self, message: Any) -> None:
        self.calls.append(("send", message["To"], message["Subject"], message.get_content()))


def test_send_email(monkeypatch: MonkeyPatch) -> None:
    FakeSmtp.sent = []
    monkeypatch.setattr(escalation.smtplib, "SMTP", FakeSmtp)
    monkeypatch.setattr(settings, "smtp_host", "mail.example.com")
    monkeypatch.setattr(settings, "smtp_username", "relay")
    monkeypatch.setattr(settings, "smtp_password", "secret")
    content = escalation.describe("dr", 7, "high", "sla_breach", "contain")

    escalation.send_email(["a@example.com", "b@example.com"], content)

    assert FakeSmtp.sent == [
        [
            ("connect", "mail.example.com", 587),
            ("starttls",),
            ("login", "relay", "secret"),
            (
                "send",
                "a@example.com, b@example.com",
                "[dr] Case #7 (high): contain SLA breach",
                "Case #7 (high): contain SLA breach\n\nhttp://localhost:8080/t/dr/cases/7\n",
            ),
        ]
    ]


def test_send_email_requires_configuration(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "smtp_host", None)
    with pytest.raises(RuntimeError, match="SMTP is not configured"):
        escalation.send_email(["a@example.com"], {})


def test_post_webhook(monkeypatch: MonkeyPatch) -> None:
    calls: list[tuple[str, Any]] = []

    class Response:
        def raise_for_status(self) -> None:
            calls.append(("checked", None))

    def post(url: str, json: Any, timeout: int) -> Response:
        calls.append((url, json))
        return Response()

    monkeypatch.setattr(escalation.httpx2, "post", post)
    escalation.post_webhook("https://hooks.example.com/x", {"a": 1})
    assert calls == [("https://hooks.example.com/x", {"a": 1}), ("checked", None)]
