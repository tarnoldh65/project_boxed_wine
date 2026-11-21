"""Jira issues and Confluence pages made from cases

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-20
"""

from alembic import op

from app.rls import tenant_rls

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | None = None
depends_on: str | None = None

# Rows go when their case is purged; the purge function deletes the case itself last.
UPGRADE = [
    """
    CREATE TABLE jira_issues (
        case_id uuid PRIMARY KEY REFERENCES cases ON DELETE CASCADE,
        tenant_id uuid NOT NULL REFERENCES tenants,
        connection text NOT NULL,
        issue_key text NOT NULL,
        url text NOT NULL,
        created_by text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE confluence_pages (
        case_id uuid NOT NULL REFERENCES cases ON DELETE CASCADE,
        kind text NOT NULL,
        tenant_id uuid NOT NULL REFERENCES tenants,
        connection text NOT NULL,
        page_id text NOT NULL,
        url text NOT NULL,
        version integer NOT NULL,
        published_by text NOT NULL,
        published_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (case_id, kind)
    )
    """,
    "GRANT SELECT, INSERT, DELETE ON jira_issues TO casemgr_app",
    "GRANT UPDATE (case_id) ON jira_issues TO casemgr_app",
    "GRANT SELECT, INSERT, DELETE ON confluence_pages TO casemgr_app",
    "GRANT UPDATE (version, url, published_by, published_at) ON confluence_pages TO casemgr_app",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)
    for table in ("jira_issues", "confluence_pages"):
        for statement in tenant_rls(table):
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE confluence_pages")
    op.execute("DROP TABLE jira_issues")
