import io
from typing import Any

import docx
import pytest
from httpx2 import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app import reports
from tests.conftest import MakeUser, SignedIn, audit_actions

SCRIPT = "<script>alert(1)</script> **bold** [x](javascript:alert(2)) \x07bell"


@pytest.fixture
async def analyst(make_user: MakeUser, tenant: str) -> SignedIn:
    return await make_user({tenant: ["analyst"]})


async def call(client: AsyncClient, method: str, path: str, body: Any, status: int = 201) -> Any:
    response = await client.request(method, path, json=body)
    assert response.status_code == status, response.text
    return response.json()


async def incident(client: AsyncClient, tenant: str, privileged_note: bool = False) -> int:
    base = f"/api/tenants/{tenant}/cases"
    case = await call(client, "POST", base, {"template": "bec", "title": "Überweisung Финансы"})
    number = case["number"]
    url = f"{base}/{number}"
    await call(client, "POST", f"{url}/notes", {"body": SCRIPT, "privileged": privileged_note})
    await call(
        client,
        "POST",
        f"{url}/timeline",
        {"occurred_at": "2026-10-01T00:00:00Z", "source": "M365", "description": "Rule added",
         "milestone": "first_compromise"},
    )  # fmt: skip
    await call(client, "PUT", f"{url}/pir", {"root_cause": "No MFA on the CFO mailbox"}, 200)
    await call(client, "POST", f"{url}/pir/actions", {"title": "Enforce MFA"})
    return number


def docx_text(body: bytes) -> tuple[str, str]:
    document = docx.Document(io.BytesIO(body))
    header = document.sections[0].header.paragraphs[0].text
    parts = [p.text for p in document.paragraphs]
    parts += [cell.text for table in document.tables for row in table.rows for cell in row.cells]
    return header, "\n".join(parts)


@pytest.mark.parametrize("kind", list(reports.Kind))
async def test_reports_render_in_both_formats(
    analyst: SignedIn, tenant: str, owner: AsyncEngine, kind: str
) -> None:
    number = await incident(analyst.client, tenant)
    url = f"/api/tenants/{tenant}/cases/{number}/reports/{kind}"

    pdf = await analyst.client.get(url)
    assert pdf.status_code == 200, pdf.text
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF-")
    assert f'filename="case-{number}-{kind}.pdf"' in pdf.headers["content-disposition"]

    word = await analyst.client.get(url, params={"format": "docx"})
    assert word.status_code == 200
    header, body = docx_text(word.content)
    assert header == "TLP:AMBER"
    assert "Überweisung Финансы" in body
    assert "report.generate" in await audit_actions(owner, f"user:{analyst.id}")


async def test_technical_report_contents(analyst: SignedIn, tenant: str) -> None:
    number = await incident(analyst.client, tenant)
    url = f"/api/tenants/{tenant}/cases/{number}/reports/technical"
    _, body = docx_text((await analyst.client.get(url, params={"format": "docx"})).content)

    # Note Markdown is kept as source text; control characters are dropped.
    assert "<script>alert(1)</script> **bold** [x](javascript:alert(2)) bell" in body
    assert "Rule added" in body
    assert "Business email compromise" in body


async def test_pir_report_contents(analyst: SignedIn, tenant: str) -> None:
    number = await incident(analyst.client, tenant)
    url = f"/api/tenants/{tenant}/cases/{number}/reports/pir"
    _, body = docx_text((await analyst.client.get(url, params={"format": "docx"})).content)

    assert "No MFA on the CFO mailbox" in body
    assert "Enforce MFA" in body
    assert "Not recorded." in body  # sections left empty


@pytest.mark.parametrize("privileged", ["case", "note"])
async def test_privileged_reports_are_stamped(
    analyst: SignedIn, make_user: MakeUser, tenant: str, privileged: str
) -> None:
    number = await incident(analyst.client, tenant, privileged_note=privileged == "note")
    if privileged == "case":
        counsel = await make_user({tenant: ["legal"]})
        await call(
            counsel.client,
            "PUT",
            f"/api/tenants/{tenant}/cases/{number}/privilege",
            {"on": True, "reason": "Counsel engaged"},
            200,
        )
    url = f"/api/tenants/{tenant}/cases/{number}/reports/executive"

    header, _ = docx_text((await analyst.client.get(url, params={"format": "docx"})).content)

    assert header == f"TLP:AMBER | {reports.PRIVILEGE_NOTICE}"


async def test_unknown_kinds_and_hidden_cases(
    analyst: SignedIn, make_user: MakeUser, tenant: str
) -> None:
    number = await incident(analyst.client, tenant)
    base = f"/api/tenants/{tenant}/cases/{number}/reports"
    assert (await analyst.client.get(f"{base}/forecast")).status_code == 422
    assert (await analyst.client.get(f"{base}/pir", params={"format": "txt"})).status_code == 422
    lead = await make_user({tenant: ["ir_lead", "restricted_case"]})
    await call(
        lead.client, "PATCH", f"/api/tenants/{tenant}/cases/{number}", {"restricted": True}, 200
    )
    assert (await analyst.client.get(f"{base}/pir")).status_code == 404
