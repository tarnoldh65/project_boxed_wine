"""timeline, notes with revisions, comments, privilege

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-01
"""

from alembic import op

from app.rls import tenant_rls

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | None = None
depends_on: str | None = None

UPGRADE = [
    "ALTER TABLE cases ADD COLUMN privileged boolean NOT NULL DEFAULT false",
    """
    CREATE TABLE timeline_events (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenants,
        case_id uuid NOT NULL REFERENCES cases,
        occurred_at timestamptz NOT NULL,
        source text NOT NULL,
        description text NOT NULL,
        milestone text,
        observable_id uuid REFERENCES observables,
        asset_id uuid REFERENCES assets,
        evidence_id uuid REFERENCES evidence,
        recorded_by text NOT NULL,
        recorded_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX timeline_events_case_idx ON timeline_events (case_id, occurred_at)",
    """
    CREATE UNIQUE INDEX timeline_events_milestone_idx ON timeline_events (case_id, milestone)
    WHERE milestone IS NOT NULL
    """,
    """
    CREATE TABLE notes (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenants,
        case_id uuid NOT NULL REFERENCES cases,
        body text NOT NULL,
        privileged boolean NOT NULL,
        revision integer NOT NULL,
        created_by text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_by text NOT NULL,
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX notes_case_idx ON notes (case_id, created_at)",
    """
    CREATE TABLE note_revisions (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        tenant_id uuid NOT NULL REFERENCES tenants,
        note_id uuid NOT NULL REFERENCES notes,
        revision integer NOT NULL,
        body text NOT NULL,
        privileged boolean NOT NULL,
        edited_by text NOT NULL,
        edited_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (note_id, revision)
    )
    """,
    """
    CREATE TRIGGER note_revisions_append_only BEFORE UPDATE OR DELETE ON note_revisions
    FOR EACH ROW EXECUTE FUNCTION reject_change()
    """,
    """
    CREATE TABLE comments (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenants,
        case_id uuid NOT NULL REFERENCES cases,
        body text NOT NULL,
        author text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX comments_case_idx ON comments (case_id, created_at)",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON timeline_events TO casemgr_app",
    "GRANT SELECT, INSERT, UPDATE ON notes TO casemgr_app",
    "GRANT SELECT, INSERT ON note_revisions TO casemgr_app",
    # Comments are a record of the conversation: they can be moved with a case, never edited.
    "GRANT SELECT, INSERT, UPDATE (case_id) ON comments TO casemgr_app",
]

TENANT_TABLES = ["timeline_events", "notes", "note_revisions", "comments"]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)
    for table in TENANT_TABLES:
        for statement in tenant_rls(table):
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE comments, note_revisions, notes, timeline_events")
    op.execute("ALTER TABLE cases DROP COLUMN privileged")
