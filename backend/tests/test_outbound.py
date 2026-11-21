import base64
import json
from typing import Any

import httpx2
import pytest
from httpx2 import AsyncClient
from pytest import MonkeyPatch
from sqlalchemy.ext.asyncio import AsyncEngine

from app import atlassian, connections
from app.reports import Fields, Heading, Paragraph, Report, Table
from tests.conftest import MakeUser, SignedIn, audit_actions

TOKEN = "atlassian-token-do-not-leak"
JIRA = "https://acme.atlassian.net"
WIKI = "https://wiki.acme.example"


class Atlassian:
    """Stands in for Jira and Confluence: records requests and answers from a queue."""

    def __init__(self, *answers: httpx2.Response | Exception) -> None:
        self.answers = list(answers)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, method: str, url: str, *, headers: dict[str, str], json: Any,
                 timeout: int) -> httpx2.Response:  # fmt: skip
        self.calls.append({"method": method, "url": url, "headers": headers, "json": json})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def answer(status: int, body: Any = None) -> httpx2.Response:
    return httpx2.Response(status, json=body) if body is not None else httpx2.Response(status)


@pytest.fixture(autouse=True)
def connected(monkeypatch: MonkeyPatch, tenant: str) -> None:
    found = connections.parse(
        json.dumps({
            "jira": {"type": "jira", "base_url": f"{JIRA}/", "auth": "basic",
                     "user": "svc@acme.com", "token": TOKEN, "tenants": [tenant]},
            "jira-dc": {"type": "jira", "base_url": "https://jira.acme.example", "auth": "bearer",
                        "token": TOKEN, "tenants": [tenant]},
            "wiki": {"type": "confluence", "base_url": WIKI, "auth": "bearer", "token": TOKEN,
                     "tenants": [tenant]},
            "other-jira": {"type": "jira", "base_url": JIRA, "auth": "bearer", "token": TOKEN,
                           "tenants": ["elsewhere"]},
        })
    )  # fmt: skip
    monkeypatch.setattr(connections, "CONNECTIONS", found)


@pytest.fixture
def remote(monkeypatch: MonkeyPatch) -> list[Atlassian]:
    """Set `remote[0]` to the fake for the test's requests."""
    holder: list[Atlassian] = [Atlassian()]
    monkeypatch.setattr(atlassian.httpx2, "request", lambda *a, **k: holder[0](*a, **k))
    return holder


@pytest.fixture
async def admin(make_user: MakeUser, tenant: str) -> SignedIn:
    """Sets up Jira and Confluence for the tenant."""
    signed_in = await make_user({tenant: ["admin"]})
    base = f"/api/tenants/{tenant}/config"
    jira = {"connection": "jira", "project_key": "SOC"}
    wiki = {"connection": "wiki", "space_key": "IR", "parent_page_id": "4242"}
    assert (await signed_in.client.put(f"{base}/jira", json=jira)).status_code == 200
    assert (await signed_in.client.put(f"{base}/confluence", json=wiki)).status_code == 200
    return signed_in


@pytest.fixture
async def analyst(make_user: MakeUser, tenant: str, admin: SignedIn) -> SignedIn:
    return await make_user({tenant: ["analyst"]})


async def open_case(client: AsyncClient, tenant: str, **body: Any) -> int:
    response = await client.post(
        f"/api/tenants/{tenant}/cases",
        json={"template": "phishing", "title": "Payroll <phish> & co"} | body,
    )
    assert response.status_code == 201, response.text
    return response.json()["number"]


def case_url(tenant: str, number: int, path: str = "") -> str:
    return f"/api/tenants/{tenant}/cases/{number}{path}"


# --- Settings -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("section", "body"),
    [
        ("jira", {"connection": "wiki", "project_key": "SOC"}),
        ("jira", {"connection": "other-jira", "project_key": "SOC"}),
        ("confluence", {"connection": "jira", "space_key": "IR"}),
    ],
)
async def test_settings_name_a_connection_of_the_right_kind(
    make_user: MakeUser, tenant: str, section: str, body: dict[str, str]
) -> None:
    admin = await make_user({tenant: ["admin"]})

    response = await admin.client.put(f"/api/tenants/{tenant}/config/{section}", json=body)

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "connection"]


async def test_settings_are_for_managers_and_can_be_cleared(
    admin: SignedIn, analyst: SignedIn, tenant: str
) -> None:
    url = f"/api/tenants/{tenant}/config/jira"
    body = {"connection": "jira", "project_key": "SOC"}
    assert (await analyst.client.put(url, json=body)).status_code == 403

    response = await admin.client.put(url, json=None)

    assert response.status_code == 200
    assert response.json()["jira"] is None
    assert response.json()["confluence"]["space_key"] == "IR"


# --- Jira ---------------------------------------------------------------------------------


async def test_creates_one_jira_issue_per_case(
    analyst: SignedIn, tenant: str, owner: AsyncEngine, remote: list[Atlassian]
) -> None:
    remote[0] = Atlassian(answer(201, {"id": "10001", "key": "SOC-7"}))
    number = await open_case(analyst.client, tenant)

    response = await analyst.client.post(case_url(tenant, number, "/jira"))

    assert response.status_code == 200, response.text
    [request] = remote[0].calls
    assert (request["method"], request["url"]) == ("POST", f"{JIRA}/rest/api/2/issue")
    pair = base64.b64encode(f"svc@acme.com:{TOKEN}".encode()).decode()
    assert request["headers"]["Authorization"] == f"Basic {pair}"
    fields = request["json"]["fields"]
    assert fields["project"] == {"key": "SOC"}
    assert fields["issuetype"] == {"name": "Task"}
    assert fields["summary"] == f"[#{number}] Payroll <phish> & co"
    assert fields["labels"] == ["casemgr", "severity-high"]
    assert "Category: phishing" in fields["description"]
    assert f"/t/{tenant}/cases/{number}" in fields["description"]
    case = response.json()
    assert case["jira"]["issue_key"] == "SOC-7"
    assert case["jira"]["url"] == f"{JIRA}/browse/SOC-7"
    assert "Jira issue created: SOC-7" in [e["description"] for e in case["timeline"]]
    assert "jira.create" in await audit_actions(owner, f"user:{analyst.id}")

    again = await analyst.client.post(case_url(tenant, number, "/jira"))

    assert again.json()["jira"]["issue_key"] == "SOC-7"
    assert len(remote[0].calls) == 1


async def test_data_center_uses_a_bearer_token(
    admin: SignedIn, tenant: str, remote: list[Atlassian]
) -> None:
    body = {"connection": "jira-dc", "project_key": "IR", "issue_type": "Incident"}
    await admin.client.put(f"/api/tenants/{tenant}/config/jira", json=body)
    remote[0] = Atlassian(answer(201, {"id": "1", "key": "IR-1"}))
    number = await open_case(admin.client, tenant)

    response = await admin.client.post(case_url(tenant, number, "/jira"))

    assert response.json()["jira"]["url"] == "https://jira.acme.example/browse/IR-1"
    [request] = remote[0].calls
    assert request["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert request["json"]["fields"]["issuetype"] == {"name": "Incident"}


async def test_jira_must_be_set_up(make_user: MakeUser, tenant: str) -> None:
    lead = await make_user({tenant: ["ir_lead"]})
    number = await open_case(lead.client, tenant)

    response = await lead.client.post(case_url(tenant, number, "/jira"))

    assert response.status_code == 422
    assert response.json()["detail"][0]["msg"] == "Jira is not set up for this tenant"


@pytest.mark.parametrize(
    ("status", "body", "detail"),
    [
        (400, {"errorMessages": [], "errors": {"project": "project is required"}},
         "Jira refused the request (HTTP 400): project: project is required"),
        (401, None, "Jira refused the request (HTTP 401)"),
        (502, {"message": "upstream down"}, "Jira refused the request (HTTP 502): upstream down"),
    ],
)  # fmt: skip
async def test_jira_refusals_are_reported_readably(
    analyst: SignedIn, tenant: str, remote: list[Atlassian], status: int, body: Any,
    detail: str,
) -> None:  # fmt: skip
    remote[0] = Atlassian(answer(status, body))
    number = await open_case(analyst.client, tenant)

    response = await analyst.client.post(case_url(tenant, number, "/jira"))

    assert (response.status_code, response.json()["detail"]) == (502, detail)
    assert (await analyst.client.get(case_url(tenant, number))).json()["jira"] is None


async def test_unreachable_jira_is_reported_without_credentials(
    analyst: SignedIn, tenant: str, remote: list[Atlassian]
) -> None:
    remote[0] = Atlassian(httpx2.ConnectError(f"failed for {TOKEN}"))
    number = await open_case(analyst.client, tenant)

    response = await analyst.client.post(case_url(tenant, number, "/jira"))

    assert response.status_code == 502
    assert response.json()["detail"] == "Could not reach Jira (ConnectError)"


async def test_restricted_and_red_cases_stay_here(
    make_user: MakeUser, admin: SignedIn, tenant: str, remote: list[Atlassian]
) -> None:
    lead = await make_user({tenant: ["ir_lead", "restricted_case"]})
    red = await open_case(lead.client, tenant)
    restricted = await open_case(lead.client, tenant)
    for number, change in ((red, {"tlp": "red"}), (restricted, {"restricted": True})):
        assert (await lead.client.patch(case_url(tenant, number), json=change)).is_success

    for number, problem in ((red, "TLP:RED"), (restricted, "restricted")):
        for path in ("/jira", "/reports/executive/confluence"):
            response = await lead.client.post(case_url(tenant, number, path))
            assert response.status_code == 422, path
            assert problem in response.json()["detail"][0]["msg"]
    assert remote[0].calls == []


async def test_privileged_cases_leave_only_with_legal_say(
    make_user: MakeUser, analyst: SignedIn, tenant: str, remote: list[Atlassian]
) -> None:
    lead = await make_user({tenant: ["ir_lead"]})
    number = await open_case(analyst.client, tenant)
    privilege = {"on": True, "reason": "Counsel engaged"}
    await lead.client.put(case_url(tenant, number, "/privilege"), json=privilege)

    assert (await analyst.client.post(case_url(tenant, number, "/jira"))).status_code == 403

    remote[0] = Atlassian(answer(201, {"id": "1", "key": "SOC-9"}))
    assert (await lead.client.post(case_url(tenant, number, "/jira"))).status_code == 200


async def test_merges_move_the_issue_to_a_target_without_one(
    analyst: SignedIn, tenant: str, remote: list[Atlassian]
) -> None:
    remote[0] = Atlassian(answer(201, {"id": "1", "key": "SOC-1"}))
    source = await open_case(analyst.client, tenant)
    target = await open_case(analyst.client, tenant)
    await analyst.client.post(case_url(tenant, source, "/jira"))

    merged = await analyst.client.post(case_url(tenant, source, "/merge"), json={"into": target})

    assert merged.status_code == 200, merged.text
    assert merged.json()["jira"]["issue_key"] == "SOC-1"


# --- Confluence ---------------------------------------------------------------------------


def test_storage_format_escapes_every_block() -> None:
    report = Report(
        "Report",
        ["TLP:AMBER", "<b>Privileged</b>"],
        [
            Heading("A & B"),
            Paragraph("one <script>\ntwo"),
            Fields([("Owner", "<img src=x>")]),
            Table(["IOC", "Type"], [["evil.example/<x>", "url"]]),
        ],
    )

    assert atlassian.storage(report) == (
        '<ac:structured-macro ac:name="note"><ac:rich-text-body>'
        "<p><strong>TLP:AMBER</strong></p><p><strong>&lt;b&gt;Privileged&lt;/b&gt;</strong></p>"
        "</ac:rich-text-body></ac:structured-macro>"
        "<h2>A &amp; B</h2>"
        "<p>one &lt;script&gt;<br />two</p>"
        "<table><tbody><tr><th>Owner</th><td>&lt;img src=x&gt;</td></tr></tbody></table>"
        "<table><tbody><tr><th>IOC</th><th>Type</th></tr>"
        "<tr><td>evil.example/&lt;x&gt;</td><td>url</td></tr></tbody></table>"
    )


def page(page_id: str = "777", version: int = 1) -> dict[str, Any]:
    return {
        "id": page_id,
        "version": {"number": version},
        "_links": {"base": WIKI, "webui": f"/spaces/IR/pages/{page_id}"},
    }


async def test_publishes_a_report_and_updates_it_in_place(
    analyst: SignedIn, tenant: str, owner: AsyncEngine, remote: list[Atlassian]
) -> None:
    number = await open_case(analyst.client, tenant)
    url = case_url(tenant, number, "/reports/executive/confluence")
    remote[0] = Atlassian(answer(200, page()))

    response = await analyst.client.post(url)

    assert response.status_code == 200, response.text
    [create] = remote[0].calls
    assert (create["method"], create["url"]) == ("POST", f"{WIKI}/rest/api/content")
    assert create["headers"]["Authorization"] == f"Bearer {TOKEN}"
    body = create["json"]
    assert body["title"] == f"Case #{number} - Executive summary (Test)"
    assert (body["type"], body["space"], body["ancestors"]) == ("page", {"key": "IR"},
                                                                [{"id": "4242"}])  # fmt: skip
    storage = body["body"]["storage"]
    assert storage["representation"] == "storage"
    assert storage["value"].startswith('<ac:structured-macro ac:name="note">')
    assert "Payroll &lt;phish&gt; &amp; co" in storage["value"]
    [published] = response.json()["confluence"]
    assert published["kind"] == "executive"
    assert (published["url"], published["version"]) == (f"{WIKI}/spaces/IR/pages/777", 1)

    remote[0] = Atlassian(answer(200, page(version=3)), answer(200, page(version=4)))
    again = await analyst.client.post(url)

    get, put = remote[0].calls
    assert (get["method"], get["url"]) == ("GET", f"{WIKI}/rest/api/content/777?expand=version")
    assert (put["method"], put["url"]) == ("PUT", f"{WIKI}/rest/api/content/777")
    assert put["json"]["version"] == {"number": 4}
    assert put["json"]["id"] == "777"
    [updated] = again.json()["confluence"]
    assert updated["version"] == 4
    timeline = [e["description"] for e in again.json()["timeline"]]
    assert "Executive summary published to Confluence" in timeline
    assert "Executive summary updated in Confluence (version 4)" in timeline
    assert (await audit_actions(owner, f"user:{analyst.id}")).count("report.publish") == 2


async def test_a_page_deleted_in_confluence_is_published_afresh(
    analyst: SignedIn, tenant: str, remote: list[Atlassian]
) -> None:
    number = await open_case(analyst.client, tenant)
    url = case_url(tenant, number, "/reports/technical/confluence")
    remote[0] = Atlassian(answer(200, page("1")))
    await analyst.client.post(url)
    remote[0] = Atlassian(answer(404, {"message": "No content found"}), answer(200, page("2")))

    response = await analyst.client.post(url)

    assert [c["method"] for c in remote[0].calls] == ["GET", "POST"]
    [published] = response.json()["confluence"]
    assert published["url"] == f"{WIKI}/spaces/IR/pages/2"


async def test_privileged_notes_make_a_report_privileged(
    make_user: MakeUser, analyst: SignedIn, tenant: str, remote: list[Atlassian]
) -> None:
    number = await open_case(analyst.client, tenant)
    note = {"body": "Counsel advises", "privileged": True}
    assert (await analyst.client.post(case_url(tenant, number, "/notes"), json=note)).is_success
    url = case_url(tenant, number, "/reports/executive/confluence")

    assert (await analyst.client.post(url)).status_code == 403

    lead = await make_user({tenant: ["ir_lead"]})
    remote[0] = Atlassian(answer(200, page()))
    assert (await lead.client.post(url)).status_code == 200
    assert "Attorney-Client" in remote[0].calls[0]["json"]["body"]["storage"]["value"]
