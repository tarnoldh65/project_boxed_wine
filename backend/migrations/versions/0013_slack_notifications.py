"""Slack destinations for escalation notifications

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-20
"""

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Connection names only: the webhook URLs stay in the deployment secret.
    op.execute("ALTER TABLE notifications ADD COLUMN slack text[] NOT NULL DEFAULT '{}'")


def downgrade() -> None:
    op.execute("ALTER TABLE notifications DROP COLUMN slack")
