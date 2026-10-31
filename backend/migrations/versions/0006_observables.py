"""observables

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-31
"""

from alembic import op

from app.rls import tenant_rls

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: str | None = None

UPGRADE = [
    """
    CREATE TABLE observables (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenants,
        type text NOT NULL,
        value text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, type, value)
    )
    """,
    """
    CREATE TABLE case_observables (
        case_id uuid NOT NULL REFERENCES cases,
        observable_id uuid NOT NULL REFERENCES observables,
        tenant_id uuid NOT NULL REFERENCES tenants,
        added_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (case_id, observable_id)
    )
    """,
    "CREATE INDEX case_observables_observable_idx ON case_observables (observable_id)",
    "GRANT SELECT, INSERT ON observables TO casemgr_app",
    "GRANT SELECT, INSERT, DELETE ON case_observables TO casemgr_app",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)
    for table in ("observables", "case_observables"):
        for statement in tenant_rls(table):
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE case_observables, observables")
