import json
from typing import Any

import httpx2
import pytest
from httpx2 import AsyncClient
from pytest import MonkeyPatch
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app import connections, escalation
from app.jobs import sla as sla_job
from app.models import Tenant
from tests.conftest import MakeUser, SignedIn

SECRET_URL = "https://hooks.slack.com/services/T000/B000/super-secret-path"
TOKEN = "atlassian-token-do-not-leak"


@pytest.fixture
def connected(monkeypatch: MonkeyPatch, tenant: str) -> dict[str, Any]:
    """Connections for the test's tenant, and one it may not use."""
    found = connections.parse(
        json.dumps({
            "slack-soc": {"type": "slack", "webhook_url": SECRET_URL, "tenants": [tenant]},
            "slack-other": {"type": "slack", "webhook_url": SECRET_URL, "tenants": ["elsewhere"]},
            "wiki": {"type": "confluence", "base_url": "https://acme.atlassian.net/wiki",
                     "auth": "bearer", "token": TOKEN, "tenants": [tenant]},
        })
    )  # fmt: skip
    monkeypatch.setattr(connections, "CONNECTIONS", found)
    return found


@pytest.fixture
async def admin(make_user: MakeUser, tenant: str) -> SignedIn:
    return await make_user({tenant: ["admin"]})


# --- The setting --------------------------------------------------------------------------


def test_parses_slack_and_atlassian_connections() -> None:
    found = connections.parse(
        json.dumps({
            "slack-soc": {"type": "slack", "webhook_url": SECRET_URL, "tenants": ["dr"]},
            "jira": {"type": "jira", "base_url": "https://acme.atlassian.net", "auth": "basic",
                     "user": "svc@acme.com", "token": TOKEN, "tenants": ["dr", "it"]},
        })
    )  # fmt: skip
    assert {name: c.type for name, c in found.items()} == {"slack-soc": "slack", "jira": "jira"}
    assert TOKEN not in repr(found)
    assert SECRET_URL not in repr(found)


def test_an_empty_setting_means_no_connections() -> None:
    assert connections.parse("{}") == {}


@pytest.mark.parametrize(
    ("value", "problem"),
    [
        ("{nope", "not valid JSON"),
        ('{"s": {"type": "slack", "webhook_url": "http://hooks.example/x", "tenants": ["dr"]}}',
         "webhook_url must use https"),
        ('{"j": {"type": "jira", "base_url": "https://a.example", "auth": "basic", '
         f'"token": "{TOKEN}", "tenants": ["dr"]}}}}', "basic auth needs a user"),
        ('{"j": {"type": "jira", "base_url": "http://a.example", "auth": "bearer", '
         f'"token": "{TOKEN}", "tenants": ["dr"]}}}}', "base_url must use https"),
        ('{"s": {"type": "teams", "tenants": ["dr"]}}', "does not match any of the expected tags"),
        (f'{{"s": {{"type": "slack", "webhook_url": "{SECRET_URL}", "tenants": []}}}}',
         "at least 1 item"),
        (f'{{"Bad Name": {{"type": "slack", "webhook_url": "{SECRET_URL}", "tenants": ["dr"]}}}}',
         "should match pattern"),
        (f'{{"s": {{"type": "slack", "webhook_url": "{SECRET_URL}", "tenants": ["dr"], '
         f'"token": "{TOKEN}"}}}}', "Extra inputs are not permitted"),
    ],
)  # fmt: skip
def test_rejects_invalid_settings_without_repeating_secrets(value: str, problem: str) -> None:
    with pytest.raises(RuntimeError, match="CASEMGR_CONNECTIONS") as raised:
        connections.parse(value)
    assert problem in str(raised.value)
    assert TOKEN not in str(raised.value)
    assert "super-secret-path" not in str(raised.value)


def test_tenants_see_only_their_connections(connected: dict[str, Any], tenant: str) -> None:
    assert list(connections.available(tenant)) == ["slack-soc", "wiki"]
    assert list(connections.available(tenant, "slack")) == ["slack-soc"]
    assert list(connections.available("elsewhere")) == ["slack-other"]
    assert connections.available("nobody") == {}


# --- API ----------------------------------------------------------------------------------


async def test_admins_list_connection_names_and_types_only(
    connected: dict[str, Any], admin: SignedIn, make_user: MakeUser, tenant: str
) -> None:
    url = f"/api/tenants/{tenant}/config/connections"
    response = await admin.client.get(url)
    assert response.status_code == 200
    assert response.json() == [
        {"name": "slack-soc", "type": "slack"},
        {"name": "wiki", "type": "confluence"},
    ]
    assert "secret" not in response.text and TOKEN not in response.text

    analyst = await make_user({tenant: ["analyst"]})
    assert (await analyst.client.get(url)).status_code == 403


async def test_rules_name_only_slack_connections_the_tenant_may_use(
    connected: dict[str, Any], admin: SignedIn, tenant: str, owner: AsyncEngine
) -> None:
    url = f"/api/tenants/{tenant}/config/escalation-rules"
    rule = {"name": "Page", "trigger": "major_incident", "slack": ["slack-soc"]}
    saved = await admin.client.put(url, json=[rule])
    assert saved.status_code == 200, saved.text
    assert saved.json()["escalation_rules"][0]["slack"] == ["slack-soc"]

    for name in ("slack-other", "wiki", "missing"):
        refused = await admin.client.put(url, json=[rule | {"slack": [name]}])
        assert refused.status_code == 422
        assert refused.json()["detail"][0]["loc"] == ["body", 0, "slack", 0]

    # The audit trail records connection names, never their URLs.
    async with owner.connect() as conn:
        audited = await conn.scalar(
            text("SELECT detail::text FROM audit_log WHERE action = 'config.update' "
                 "ORDER BY id DESC LIMIT 1")
        )  # fmt: skip
    assert "slack-soc" in audited and "super-secret-path" not in audited


async def test_a_slack_only_rule_is_enough(
    connected: dict[str, Any], admin: SignedIn, tenant: str
) -> None:
    rule = {"name": "Slack only", "trigger": "case_created", "slack": ["slack-soc"]}
    response = await admin.client.put(f"/api/tenants/{tenant}/config/escalation-rules", json=[rule])
    assert response.status_code == 200, response.text
    empty = await admin.client.put(
        f"/api/tenants/{tenant}/config/escalation-rules",
        json=[{"name": "Nothing", "trigger": "case_created"}],
    )
    assert "needs an email, a webhook, Slack, or a reassignment" in empty.text


# --- Delivery -----------------------------------------------------------------------------


class Posted:
    def __init__(self, status: int = 200, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.status = status
        self.error = error

    def __call__(self, url: str, json: Any, timeout: int) -> httpx2.Response:
        self.calls.append((url, json))
        if self.error:
            raise self.error
        return httpx2.Response(self.status)


async def page_on_new_cases(admin: SignedIn, tenant: str) -> None:
    rule = {"name": "New", "trigger": "case_created", "slack": ["slack-soc"]}
    response = await admin.client.put(f"/api/tenants/{tenant}/config/escalation-rules", json=[rule])
    assert response.status_code == 200, response.text


async def open_case(client: AsyncClient, tenant: str, **body: Any) -> int:
    response = await client.post(
        f"/api/tenants/{tenant}/cases",
        json={"template": "phishing", "title": "Payroll <phish> & co"} | body,
    )
    assert response.status_code == 201, response.text
    return response.json()["number"]


async def tenant_row(owner: AsyncEngine, slug: str) -> Tenant:
    async with owner.connect() as conn:
        row = (await conn.execute(text("SELECT id, slug, name FROM tenants WHERE slug = :s"),
                                  {"s": slug})).one()  # fmt: skip
    return Tenant(id=row.id, slug=row.slug, name=row.name)


async def test_delivers_a_slack_message_with_the_title_escaped(
    connected: dict[str, Any], admin: SignedIn, tenant: str, owner: AsyncEngine,
    monkeypatch: MonkeyPatch,
) -> None:  # fmt: skip
    posted = Posted()
    monkeypatch.setattr(escalation.httpx2, "post", posted)
    await page_on_new_cases(admin, tenant)
    number = await open_case(admin.client, tenant)

    assert await escalation.deliver(await tenant_row(owner, tenant)) == (1, 0)
    [(url, message)] = posted.calls
    assert url == SECRET_URL
    link = f"http://localhost:8080/t/{tenant}/cases/{number}"
    assert message == {
        "text": f"Case #{number}: Payroll <phish> & co (high): case created {link}",
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn",
                         "text": f"*<{link}|Case #{number}: Payroll &lt;phish&gt; &amp; co>*"},
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": "*high*"},
                    {"type": "mrkdwn", "text": "case created"},
                    {"type": "mrkdwn", "text": tenant},
                ],
            },
        ],
    }  # fmt: skip
    # Sent once: nothing is left to deliver.
    assert await escalation.deliver(await tenant_row(owner, tenant)) == (0, 0)


async def test_restricted_cases_go_to_slack_without_their_title(
    connected: dict[str, Any], make_user: MakeUser, tenant: str, owner: AsyncEngine,
    monkeypatch: MonkeyPatch,
) -> None:  # fmt: skip
    posted = Posted()
    monkeypatch.setattr(escalation.httpx2, "post", posted)
    insider = await make_user({tenant: ["admin", "restricted_case"]})
    await page_on_new_cases(insider, tenant)
    number = await open_case(insider.client, tenant)
    response = await insider.client.patch(
        f"/api/tenants/{tenant}/cases/{number}", json={"restricted": True}
    )
    assert response.status_code == 200, response.text

    await escalation.deliver(await tenant_row(owner, tenant))
    [(_, message)] = posted.calls
    assert "Payroll" not in json.dumps(message)
    assert message["blocks"][0]["text"]["text"].endswith(f"|Case #{number}>*")


@pytest.mark.parametrize(
    ("posted", "error"),
    [
        (Posted(status=404), "Slack connection 'slack-soc' answered HTTP 404"),
        (Posted(error=httpx2.ConnectError(f"cannot reach {SECRET_URL}")),
         "Slack connection 'slack-soc' failed: ConnectError"),
    ],
)  # fmt: skip
async def test_failures_are_retried_without_recording_the_webhook_url(
    connected: dict[str, Any], admin: SignedIn, tenant: str, owner: AsyncEngine,
    monkeypatch: MonkeyPatch, posted: Posted, error: str,
) -> None:  # fmt: skip
    monkeypatch.setattr(escalation.httpx2, "post", posted)
    await page_on_new_cases(admin, tenant)
    await open_case(admin.client, tenant)

    assert await escalation.deliver(await tenant_row(owner, tenant)) == (0, 1)
    async with owner.connect() as conn:
        row = (await conn.execute(text("SELECT attempts, last_error, sent_at FROM notifications"
                                       " ORDER BY created_at DESC LIMIT 1"))).one()  # fmt: skip
    assert (row.attempts, row.last_error, row.sent_at) == (1, error, None)


async def test_a_connection_removed_after_the_rule_was_saved_is_reported(
    connected: dict[str, Any], admin: SignedIn, tenant: str, owner: AsyncEngine,
    monkeypatch: MonkeyPatch,
) -> None:  # fmt: skip
    await page_on_new_cases(admin, tenant)
    await open_case(admin.client, tenant)
    monkeypatch.setattr(connections, "CONNECTIONS", {})

    assert await escalation.deliver(await tenant_row(owner, tenant)) == (0, 1)
    async with owner.connect() as conn:
        last_error = await conn.scalar(
            text("SELECT last_error FROM notifications ORDER BY created_at DESC LIMIT 1")
        )
    assert last_error == "Slack connection 'slack-soc' is not available to this tenant"


async def test_the_sla_job_delivers_slack_too(
    connected: dict[str, Any], admin: SignedIn, tenant: str, monkeypatch: MonkeyPatch
) -> None:
    posted = Posted()
    monkeypatch.setattr(escalation.httpx2, "post", posted)
    await page_on_new_cases(admin, tenant)
    await open_case(admin.client, tenant)
    assert await sla_job.run() == 0
    assert [url for url, _ in posted.calls] == [SECRET_URL]
