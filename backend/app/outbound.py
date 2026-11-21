"""Cases going out of the tool: Jira issues and Confluence pages, and what may leave."""

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import atlassian, connections, journal, reports
from app.auth import TenantContext, deny
from app.cases import ConfluencePageOut, JiraIssueOut, invalid
from app.config import settings
from app.connections import AtlassianConnection
from app.models import Case, ConfluencePage, JiraIssue
from app.tenant_config import ConfluenceSettings, JiraSettings, TenantConfig, Tlp


def setup_problem(
    slug: str, kind: str, chosen: JiraSettings | ConfluenceSettings | None
) -> str | None:
    """Why the tenant may not use the chosen connection for this service, if it may not."""
    if chosen is not None and chosen.connection not in connections.available(slug, kind):
        return f"not a {kind.capitalize()} connection this tenant may use"
    return None


def _connection(slug: str, kind: str, name: str) -> AtlassianConnection:
    found = connections.available(slug, kind).get(name)
    if not isinstance(found, AtlassianConnection):
        invalid(f"the {kind.capitalize()} connection {name!r} is not available")
    return found


async def check_may_leave(
    db: AsyncSession, ctx: TenantContext, case: Case, privileged: bool
) -> None:
    """Restricted and TLP:RED cases stay here; privileged ones leave only with legal's say."""
    if case.restricted:
        invalid("restricted cases cannot be sent outside this tool")
    if case.tlp == Tlp.RED:
        invalid("TLP:RED cases cannot be sent outside this tool")
    if privileged and "legal:manage" not in ctx.permissions:
        await deny(db, ctx.principal, f"case:{case.number}:outbound", ctx.tenant.id)


# --- Jira ---------------------------------------------------------------------------------


def issue_fields(
    config: TenantConfig, case: Case, chosen: JiraSettings, slug: str
) -> dict[str, Any]:
    """The issue: what the case is and where it lives, without its description or notes."""
    state = next((s.name for s in config.states if s.key == case.state), case.state)
    link = f"{settings.public_url}/t/{slug}/cases/{case.number}"
    description = "\n".join([
        f"Severity: {case.severity} ({case.priority})",
        f"Category: {case.category}",
        f"State: {state}",
        f"Opened: {case.created_at:%Y-%m-%d %H:%M} UTC",
        f"Case: {link}",
    ])  # fmt: skip
    return {
        "fields": {
            "project": {"key": chosen.project_key},
            "issuetype": {"name": chosen.issue_type},
            "summary": f"[#{case.number}] {case.title}",
            "description": description,
            "labels": ["casemgr", f"severity-{case.severity}"],
        }
    }


async def create_issue(
    db: AsyncSession, ctx: TenantContext, config: TenantConfig, case: Case
) -> JiraIssue:
    """The case's Jira issue, created now if it has none."""
    existing = await db.get(JiraIssue, case.id)
    if existing is not None:
        return existing
    chosen = config.jira or invalid("Jira is not set up for this tenant")
    await check_may_leave(db, ctx, case, case.privileged)
    connection = _connection(ctx.tenant.slug, "jira", chosen.connection)
    created = atlassian.call(
        connection, "POST", "/rest/api/2/issue", issue_fields(config, case, chosen, ctx.tenant.slug)
    )
    issue = JiraIssue(
        case_id=case.id,
        tenant_id=case.tenant_id,
        connection=chosen.connection,
        issue_key=created["key"],
        url=f"{connection.base_url.rstrip('/')}/browse/{created['key']}",
        created_by=ctx.principal.actor,
    )
    db.add(issue)
    await journal.record(db, case, ctx.principal.actor, f"Jira issue created: {issue.issue_key}")
    await ctx.audit(
        db,
        "jira.create",
        f"case:{case.number}",
        issue=issue.issue_key,
        connection=chosen.connection,
    )
    await db.flush()
    return issue


async def move_issue(db: AsyncSession, source: Case, target: Case) -> bool:
    """On merge the target takes the source's issue, unless it has one of its own."""
    if await db.get(JiraIssue, target.id) is not None:
        return False
    moved = await db.execute(
        update(JiraIssue).where(JiraIssue.case_id == source.id).values(case_id=target.id)
    )
    return moved.rowcount > 0  # type: ignore[attr-defined]


# --- Confluence ---------------------------------------------------------------------------


def page_title(kind: reports.Kind, number: int, tenant: str) -> str:
    return f"Case #{number} - {reports.BUILDERS[kind][0]} ({tenant})"


async def publish(
    db: AsyncSession,
    ctx: TenantContext,
    config: TenantConfig,
    case: Case,
    kind: reports.Kind,
    report: reports.Report,
) -> ConfluencePage:
    """Publish a report as a Confluence page; publishing again adds a version to the same page."""
    chosen = config.confluence or invalid("Confluence is not set up for this tenant")
    await check_may_leave(db, ctx, case, reports.PRIVILEGE_NOTICE in report.markings)
    connection = _connection(ctx.tenant.slug, "confluence", chosen.connection)
    title = page_title(kind, case.number, ctx.tenant.name)
    body = {"storage": {"value": atlassian.storage(report), "representation": "storage"}}
    page = await db.get(ConfluencePage, (case.id, kind))
    current = None
    if page is not None and page.connection == chosen.connection:
        try:
            current = atlassian.call(
                connection, "GET", f"/rest/api/content/{page.page_id}?expand=version"
            )
        except HTTPException as error:  # a page deleted in Confluence is published afresh
            if "HTTP 404" not in str(error.detail):
                raise
    if current is not None and page is not None:
        version = current["version"]["number"] + 1
        atlassian.call(
            connection,
            "PUT",
            f"/rest/api/content/{page.page_id}",
            {
                "id": page.page_id,
                "type": "page",
                "title": title,
                "version": {"number": version},
                "body": body,
            },
        )
        page.version = version
        page.published_by = ctx.principal.actor
        page.published_at = datetime.now(UTC)
        line = f"{reports.BUILDERS[kind][0]} updated in Confluence (version {version})"
    else:
        request: dict[str, Any] = {
            "type": "page",
            "title": title,
            "space": {"key": chosen.space_key},
            "body": body,
        }
        if chosen.parent_page_id:
            request["ancestors"] = [{"id": chosen.parent_page_id}]
        created = atlassian.call(connection, "POST", "/rest/api/content", request)
        links = created.get("_links", {})
        url = (
            f"{links['base']}{links['webui']}"
            if "base" in links and "webui" in links
            else f"{connection.base_url.rstrip('/')}/pages/viewpage.action?pageId={created['id']}"
        )
        if page is not None:
            await db.delete(page)
            await db.flush()
        page = ConfluencePage(
            case_id=case.id,
            kind=kind,
            tenant_id=case.tenant_id,
            connection=chosen.connection,
            page_id=str(created["id"]),
            url=url,
            version=created.get("version", {}).get("number", 1),
            published_by=ctx.principal.actor,
        )
        db.add(page)
        line = f"{reports.BUILDERS[kind][0]} published to Confluence"
    await journal.record(db, case, ctx.principal.actor, line)
    await ctx.audit(
        db, "report.publish", f"case:{case.number}", kind=kind, page=page.page_id,
        version=page.version, connection=chosen.connection,
    )  # fmt: skip
    await db.flush()
    return page


async def for_case(
    db: AsyncSession, case: Case
) -> tuple[JiraIssueOut | None, list[ConfluencePageOut]]:
    issue = await db.get(JiraIssue, case.id)
    pages = await db.scalars(
        select(ConfluencePage)
        .where(ConfluencePage.case_id == case.id)
        .order_by(ConfluencePage.kind)
    )
    return (
        JiraIssueOut.model_validate(issue) if issue else None,
        [ConfluencePageOut.model_validate(p) for p in pages],
    )
