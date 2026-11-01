"""search, saved searches, post-incident reviews, retention purge

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-03
"""

from alembic import op

from app.rls import tenant_rls

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | None = None
depends_on: str | None = None

# Full-text search documents, kept up to date by Postgres.
SEARCH_COLUMNS = {
    "cases": "to_tsvector('english', title || ' ' || description)",
    "notes": "to_tsvector('english', body)",
    # File names split into words: badge-log.csv matches "badge log".
    "evidence": "to_tsvector('english', translate(name, '._-', '   ') || ' ' || description)",
    "observables": "to_tsvector('simple', value)",
}

# Tables holding a case's data, children before parents. Purging a case deletes from each.
PURGE = """
    DELETE FROM disclosures WHERE case_id = target;
    DELETE FROM materiality_decisions WHERE case_id = target;
    DELETE FROM regulatory_clocks WHERE case_id = target;
    DELETE FROM pir_actions WHERE case_id = target;
    DELETE FROM pirs WHERE case_id = target;
    DELETE FROM comments WHERE case_id = target;
    DELETE FROM note_revisions WHERE note_id IN (SELECT id FROM notes WHERE case_id = target);
    DELETE FROM notes WHERE case_id = target;
    DELETE FROM timeline_events WHERE case_id = target;
    DELETE FROM custody_log WHERE evidence_id IN (SELECT id FROM evidence WHERE case_id = target);
    DELETE FROM evidence WHERE case_id = target;
    DELETE FROM case_observables WHERE case_id = target;
    DELETE FROM notifications WHERE case_id = target;
    DELETE FROM tasks WHERE case_id = target;
    DELETE FROM case_assets WHERE case_id = target;
    DELETE FROM alerts WHERE case_id = target;
    UPDATE cases SET merged_into_id = NULL WHERE merged_into_id = target;
    DELETE FROM cases WHERE id = target;
"""

UPGRADE = [
    *(
        f"ALTER TABLE {table} ADD COLUMN search tsvector GENERATED ALWAYS AS ({doc}) STORED"
        for table, doc in SEARCH_COLUMNS.items()
    ),
    *(f"CREATE INDEX {table}_search_idx ON {table} USING gin (search)" for table in SEARCH_COLUMNS),
    """
    CREATE TABLE saved_searches (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenants,
        user_id uuid NOT NULL REFERENCES users,
        name text NOT NULL,
        query text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, user_id, name)
    )
    """,
    """
    CREATE TABLE pirs (
        case_id uuid PRIMARY KEY REFERENCES cases,
        tenant_id uuid NOT NULL REFERENCES tenants,
        root_cause text NOT NULL,
        contributing_factors text NOT NULL,
        went_well text NOT NULL,
        went_poorly text NOT NULL,
        updated_by text NOT NULL,
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE pir_actions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenants,
        case_id uuid NOT NULL REFERENCES cases,
        title text NOT NULL,
        owner_id uuid REFERENCES users,
        due_at timestamptz,
        done_at timestamptz,
        created_by text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX pir_actions_case_idx ON pir_actions (case_id, created_at)",
    "CREATE INDEX pir_actions_open_idx ON pir_actions (due_at) WHERE done_at IS NULL",
    "GRANT SELECT, INSERT, DELETE ON saved_searches TO casemgr_app",
    "GRANT SELECT, INSERT, UPDATE ON pirs, pir_actions TO casemgr_app",
    # Append-only history may be deleted only by a retention purge, never edited.
    """
    CREATE FUNCTION reject_change_unless_purging() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' AND current_setting('app.purging', true) = 'on' THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
    END
    $$
    """,
    *(
        f"""
        CREATE OR REPLACE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table}
        FOR EACH ROW EXECUTE FUNCTION reject_change_unless_purging()
        """
        for table in ("custody_log", "note_revisions")
    ),
    # Runs with the owner's rights, so the app role needs no DELETE grant on case data. Open
    # cases and cases under legal hold are refused here, whatever the caller decided.
    f"""
    CREATE FUNCTION purge_case(target uuid) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM cases WHERE id = target AND closed_at IS NOT NULL AND NOT legal_hold
        ) THEN
            RAISE EXCEPTION 'case % is open, under legal hold, or missing', target;
        END IF;
        PERFORM set_config('app.purging', 'on', true);
        {PURGE}
        PERFORM set_config('app.purging', 'off', true);
    END
    $$
    """,  # noqa: S608 - PURGE is a constant
    # Saved configurations get the PIR gate on their terminal states, as new tenants do.
    """
    UPDATE tenant_config SET data = jsonb_set(data, '{states}', (
        SELECT jsonb_agg(
            CASE WHEN (s->>'terminal')::boolean THEN s || '{"require_pir": true}' ELSE s END
            ORDER BY i
        )
        FROM jsonb_array_elements(data->'states') WITH ORDINALITY AS t(s, i)
    ))
    """,
    "REVOKE ALL ON FUNCTION purge_case(uuid) FROM PUBLIC",
    "GRANT EXECUTE ON FUNCTION purge_case(uuid) TO casemgr_app",
]

TENANT_TABLES = ["saved_searches", "pirs", "pir_actions"]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)
    for table in TENANT_TABLES:
        for statement in tenant_rls(table):
            op.execute(statement)


DOWNGRADE = [
    "DROP FUNCTION purge_case(uuid)",
    *(
        f"CREATE OR REPLACE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION reject_change()"
        for table in ("custody_log", "note_revisions")
    ),
    "DROP FUNCTION reject_change_unless_purging()",
    "DROP TABLE pir_actions, pirs, saved_searches",
    *(f"ALTER TABLE {table} DROP COLUMN search" for table in SEARCH_COLUMNS),
]


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
