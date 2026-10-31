"""evidence and chain of custody

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-31
"""

from alembic import op

from app.rls import tenant_rls

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | None = None
depends_on: str | None = None

UPGRADE = [
    "ALTER TABLE cases ADD COLUMN legal_hold boolean NOT NULL DEFAULT false",
    """
    CREATE TABLE evidence (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenants,
        case_id uuid NOT NULL REFERENCES cases,
        kind text NOT NULL,
        name text NOT NULL,
        description text NOT NULL,
        size bigint,
        location text,
        object_key text UNIQUE,
        upload_id text,
        status text NOT NULL,
        sha256 text,
        object_sha256 text,
        created_by text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        deleted_at timestamptz
    )
    """,
    "CREATE INDEX evidence_case_idx ON evidence (case_id)",
    "CREATE INDEX evidence_status_idx ON evidence (tenant_id, status)",
    """
    CREATE TABLE custody_log (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        tenant_id uuid NOT NULL REFERENCES tenants,
        evidence_id uuid NOT NULL REFERENCES evidence,
        at timestamptz NOT NULL,
        actor text NOT NULL,
        action text NOT NULL,
        detail jsonb NOT NULL,
        prev_hash text NOT NULL,
        hash text NOT NULL
    )
    """,
    "CREATE INDEX custody_log_evidence_idx ON custody_log (evidence_id, id)",
    """
    CREATE TRIGGER custody_log_append_only BEFORE UPDATE OR DELETE ON custody_log
    FOR EACH ROW EXECUTE FUNCTION reject_change()
    """,
    """
    CREATE TRIGGER custody_log_no_truncate BEFORE TRUNCATE ON custody_log
    FOR EACH STATEMENT EXECUTE FUNCTION reject_change()
    """,
    "GRANT SELECT, INSERT, UPDATE ON evidence TO casemgr_app",
    "GRANT SELECT, INSERT ON custody_log TO casemgr_app",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)
    for table in ("evidence", "custody_log"):
        for statement in tenant_rls(table):
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE custody_log, evidence")
    op.execute("ALTER TABLE cases DROP COLUMN legal_hold")
