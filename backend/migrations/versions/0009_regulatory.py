"""regulatory clocks, materiality decisions, notification log

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-02
"""

from alembic import op

from app.rls import tenant_rls

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | None = None
depends_on: str | None = None

UPGRADE = [
    """
    CREATE TABLE regulatory_clocks (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenants,
        case_id uuid NOT NULL REFERENCES cases,
        regime text NOT NULL,
        name text NOT NULL,
        started_at timestamptz NOT NULL,
        due_at timestamptz NOT NULL,
        warn_at timestamptz NOT NULL,
        met_at timestamptz,
        UNIQUE (case_id, regime)
    )
    """,
    "CREATE INDEX regulatory_clocks_open_idx ON regulatory_clocks (due_at) WHERE met_at IS NULL",
    """
    CREATE TABLE materiality_decisions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenants,
        case_id uuid NOT NULL REFERENCES cases,
        material boolean NOT NULL,
        decided_by text NOT NULL,
        decided_at timestamptz NOT NULL,
        rationale text NOT NULL,
        recorded_by text NOT NULL,
        recorded_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX materiality_decisions_case_idx ON materiality_decisions (case_id, recorded_at)",
    """
    CREATE TABLE disclosures (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenants,
        case_id uuid NOT NULL REFERENCES cases,
        recipient_type text NOT NULL,
        recipient text NOT NULL,
        sent_at timestamptz NOT NULL,
        summary text NOT NULL,
        clock_id uuid REFERENCES regulatory_clocks,
        evidence_ids uuid[] NOT NULL,
        recorded_by text NOT NULL,
        recorded_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX disclosures_case_idx ON disclosures (case_id, sent_at)",
    "GRANT SELECT, INSERT, UPDATE ON regulatory_clocks TO casemgr_app",
    # Decisions and notifications sent are a record: they move with a merged case, never change.
    "GRANT SELECT, INSERT, UPDATE (case_id) ON materiality_decisions TO casemgr_app",
    "GRANT SELECT, INSERT, UPDATE (case_id) ON disclosures TO casemgr_app",
]

TENANT_TABLES = ["regulatory_clocks", "materiality_decisions", "disclosures"]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)
    for table in TENANT_TABLES:
        for statement in tenant_rls(table):
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE disclosures, materiality_decisions, regulatory_clocks")
