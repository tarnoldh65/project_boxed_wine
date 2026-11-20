"""timeline entries the app records as actions happen

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-19
"""

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE timeline_events ADD COLUMN automatic boolean NOT NULL DEFAULT false")


def downgrade() -> None:
    op.execute("ALTER TABLE timeline_events DROP COLUMN automatic")
