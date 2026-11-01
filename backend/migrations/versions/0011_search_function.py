"""search through a function that can use the full-text indexes

Row-level security keeps Postgres from using an index for a condition that is not
leakproof, and tsvector @@ tsquery is not, so every search scanned whole tables.
search_hits runs as casemgr_search, a NOLOGIN role with BYPASSRLS and SELECT on the
searched tables only, and applies the tenant and restricted-case filters itself.
The role is created by the platform, like casemgr_app (deploy/postgres/init.sql,
the casemgr-db chart, and the tests).

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-01
"""

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | None = None
depends_on: str | None = None

UPGRADE = [
    "GRANT SELECT ON cases, notes, evidence, observables, case_observables TO casemgr_search",
    """
    CREATE FUNCTION search_hits(q text, include_restricted boolean, max_hits integer)
    RETURNS TABLE (case_number integer, case_title text, kind text, text text, rank real)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
        WITH tenant AS (SELECT nullif(current_setting('app.tenant_id', true), '')::uuid AS id),
        hits AS (
            SELECT id AS case_id, 'case' AS kind, title || ' ' || description AS text,
                   ts_rank(search, websearch_to_tsquery('english', q)) AS rank
            FROM cases
            WHERE tenant_id = (SELECT id FROM tenant)
              AND search @@ websearch_to_tsquery('english', q)
            UNION ALL
            SELECT case_id, 'note', body, ts_rank(search, websearch_to_tsquery('english', q))
            FROM notes
            WHERE tenant_id = (SELECT id FROM tenant)
              AND search @@ websearch_to_tsquery('english', q)
            UNION ALL
            SELECT case_id, 'evidence', name || ' ' || description,
                   ts_rank(search, websearch_to_tsquery('english', q))
            FROM evidence
            WHERE tenant_id = (SELECT id FROM tenant)
              AND search @@ websearch_to_tsquery('english', q)
            UNION ALL
            SELECT l.case_id, 'observable', o.type || ': ' || o.value,
                   ts_rank(o.search, websearch_to_tsquery('simple', q))
            FROM observables o JOIN case_observables l ON l.observable_id = o.id
            WHERE o.tenant_id = (SELECT id FROM tenant)
              AND o.search @@ websearch_to_tsquery('simple', q)
        )
        -- The same visibility rule as app.cases.visible.
        SELECT c.number, c.title, h.kind, h.text, h.rank
        FROM hits h JOIN cases c ON c.id = h.case_id
        WHERE include_restricted OR NOT c.restricted
        ORDER BY h.rank DESC, c.number DESC
        LIMIT max_hits
    $$
    """,
    # A new owner needs CREATE on the schema only while ownership is transferred.
    "GRANT CREATE ON SCHEMA public TO casemgr_search",
    "ALTER FUNCTION search_hits(text, boolean, integer) OWNER TO casemgr_search",
    "REVOKE CREATE ON SCHEMA public FROM casemgr_search",
    "REVOKE ALL ON FUNCTION search_hits(text, boolean, integer) FROM PUBLIC",
    "GRANT EXECUTE ON FUNCTION search_hits(text, boolean, integer) TO casemgr_app",
]

DOWNGRADE = [
    "DROP FUNCTION search_hits(text, boolean, integer)",
    "REVOKE SELECT ON cases, notes, evidence, observables, case_observables FROM casemgr_search",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
