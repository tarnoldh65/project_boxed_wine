"""per-tenant configuration

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-31
"""

from alembic import op

from app.rls import tenant_rls

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE tenant_config (
            tenant_id uuid PRIMARY KEY REFERENCES tenants,
            data jsonb NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now(),
            updated_by text NOT NULL
        )
        """
    )
    for statement in tenant_rls("tenant_config"):
        op.execute(statement)
    op.execute("GRANT SELECT, INSERT, UPDATE ON tenant_config TO casemgr_app")


def downgrade() -> None:
    op.execute("DROP TABLE tenant_config")
