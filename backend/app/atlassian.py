"""Calls to Jira and Confluence (Cloud or Data Center), and Confluence page markup."""

import base64
from html import escape
from typing import Any

import httpx2
from fastapi import HTTPException

from app.connections import AtlassianConnection
from app.reports import Fields, Heading, Paragraph, Report, Table, clean

TIMEOUT_SECONDS = 20


def _headers(connection: AtlassianConnection) -> dict[str, str]:
    token = connection.token.get_secret_value()
    if connection.auth == "basic":
        pair = base64.b64encode(f"{connection.user}:{token}".encode()).decode()
        credentials = f"Basic {pair}"
    else:
        credentials = f"Bearer {token}"
    return {"Authorization": credentials, "Accept": "application/json"}


def _problem(body: Any) -> str:
    """The reasons Jira or Confluence gave for refusing a request."""
    if not isinstance(body, dict):
        return ""
    reasons = [str(m) for m in body.get("errorMessages", [])]
    reasons += [f"{field}: {message}" for field, message in (body.get("errors") or {}).items()]
    if body.get("message"):
        reasons.append(str(body["message"]))
    return "; ".join(reasons)


def call(
    connection: AtlassianConnection, method: str, path: str, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Call the service; failures become a 502 that says why, without credentials."""
    service = connection.type.capitalize()
    try:
        response = httpx2.request(
            method,
            f"{connection.base_url.rstrip('/')}{path}",
            headers=_headers(connection),
            json=body,
            timeout=TIMEOUT_SECONDS,
        )
    except httpx2.HTTPError as error:
        raise HTTPException(502, f"Could not reach {service} ({type(error).__name__})") from None
    if response.status_code >= 400:
        try:
            reason = _problem(response.json())
        except ValueError:
            reason = ""
        detail = f"{service} refused the request (HTTP {response.status_code})"
        raise HTTPException(502, f"{detail}: {reason}" if reason else detail)
    return response.json() if response.content else {}


# --- Confluence storage format ------------------------------------------------------------


def _text(value: str) -> str:
    return escape(clean(value))


def _row(cells: list[str], tag: str) -> str:
    return "<tr>" + "".join(f"<{tag}>{_text(c)}</{tag}>" for c in cells) + "</tr>"


def storage(report: Report) -> str:
    """A report as a Confluence page body. Every piece of text is escaped."""
    markings = "".join(f"<p><strong>{_text(m)}</strong></p>" for m in report.markings)
    parts = [
        '<ac:structured-macro ac:name="note"><ac:rich-text-body>'
        f"{markings}</ac:rich-text-body></ac:structured-macro>"
    ]
    for block in report.blocks:
        match block:
            case Heading(text):
                parts.append(f"<h2>{_text(text)}</h2>")
            case Paragraph(text):
                parts.append(
                    "<p>" + "<br />".join(_text(line) for line in text.split("\n")) + "</p>"
                )
            case Fields(rows):
                parts.append(
                    "<table><tbody>"
                    + "".join(
                        f"<tr><th>{_text(name)}</th><td>{_text(value)}</td></tr>"
                        for name, value in rows
                    )
                    + "</tbody></table>"
                )
            case Table(headers, rows):
                parts.append(
                    "<table><tbody>"
                    + _row(headers, "th")
                    + "".join(_row(row, "td") for row in rows)
                    + "</tbody></table>"
                )
    return "".join(parts)
