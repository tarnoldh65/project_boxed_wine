"""tasks, SLA milestones, escalation outbox

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-31
"""

from alembic import op

from app.rls import tenant_rls

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: str | None = None

UPGRADE = [
    """
    ALTER TABLE cases
        ADD COLUMN acknowledged_at timestamptz,
        ADD COLUMN triaged_at timestamptz,
        ADD COLUMN contained_at timestamptz,
        ADD COLUMN major_incident_at timestamptz,
        ADD COLUMN major_incident_reason text
    """,
    """
    CREATE TABLE tasks (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenants,
        case_id uuid NOT NULL REFERENCES cases,
        title text NOT NULL,
        description text NOT NULL,
        status text NOT NULL,
        assignee_id uuid REFERENCES users,
        due_at timestamptz,
        depends_on uuid[] NOT NULL,
        checklist jsonb NOT NULL,
        waive_reason text,
        playbook_key text,
        position integer NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        completed_at timestamptz
    )
    """,
    "CREATE INDEX tasks_case_idx ON tasks (case_id)",
    """
    CREATE UNIQUE INDEX tasks_playbook_idx ON tasks (case_id, playbook_key)
    WHERE playbook_key IS NOT NULL
    """,
    """
    CREATE TABLE notifications (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenants,
        case_id uuid NOT NULL REFERENCES cases,
        rule text NOT NULL,
        trigger text NOT NULL,
        step text NOT NULL,
        emails text[] NOT NULL,
        webhook text,
        created_at timestamptz NOT NULL DEFAULT now(),
        sent_at timestamptz,
        attempts integer NOT NULL DEFAULT 0,
        last_error text,
        UNIQUE (case_id, rule, trigger, step)
    )
    """,
    "CREATE INDEX notifications_pending_idx ON notifications (tenant_id) WHERE sent_at IS NULL",
    "GRANT SELECT, INSERT, UPDATE ON tasks TO casemgr_app",
    "GRANT SELECT, INSERT, UPDATE ON notifications TO casemgr_app",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)
    for table in ("tasks", "notifications"):
        for statement in tenant_rls(table):
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE notifications, tasks")
    op.execute(
        "ALTER TABLE cases DROP COLUMN acknowledged_at, DROP COLUMN triaged_at, "
        "DROP COLUMN contained_at, DROP COLUMN major_incident_at, DROP COLUMN major_incident_reason"
    )
