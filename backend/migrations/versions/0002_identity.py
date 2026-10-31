"""identity, tenancy, api tokens, audit log

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-30
"""

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None

# The application connects as casemgr_app, which must already exist (created by the
# database operator). It owns nothing, so it cannot bypass row-level security.
UPGRADE = [
    """
    CREATE TABLE tenants (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        slug text NOT NULL UNIQUE,
        name text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE users (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        subject text NOT NULL UNIQUE,
        username text NOT NULL,
        email text,
        display_name text NOT NULL,
        is_platform_admin boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now(),
        last_login_at timestamptz
    )
    """,
    """
    CREATE TABLE memberships (
        user_id uuid NOT NULL REFERENCES users ON DELETE CASCADE,
        tenant_id uuid NOT NULL REFERENCES tenants,
        grant_name text NOT NULL,
        PRIMARY KEY (user_id, tenant_id, grant_name)
    )
    """,
    """
    CREATE TABLE sessions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        token_hash text NOT NULL UNIQUE,
        user_id uuid NOT NULL REFERENCES users ON DELETE CASCADE,
        refresh_token text NOT NULL,
        id_token text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        last_seen_at timestamptz NOT NULL DEFAULT now(),
        synced_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE api_tokens (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenants,
        name text NOT NULL,
        prefix text NOT NULL,
        token_hash text NOT NULL UNIQUE,
        scopes text[] NOT NULL,
        created_by uuid NOT NULL REFERENCES users,
        created_at timestamptz NOT NULL DEFAULT now(),
        expires_at timestamptz NOT NULL,
        revoked_at timestamptz
    )
    """,
    "CREATE INDEX api_tokens_tenant_idx ON api_tokens (tenant_id)",
    """
    CREATE TABLE audit_log (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        at timestamptz NOT NULL,
        actor text NOT NULL,
        tenant_id uuid,
        action text NOT NULL,
        target text NOT NULL,
        detail jsonb NOT NULL,
        prev_hash text NOT NULL,
        hash text NOT NULL
    )
    """,
    """
    CREATE FUNCTION reject_change() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
    END
    $$
    """,
    """
    CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION reject_change()
    """,
    """
    CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION reject_change()
    """,
    "GRANT SELECT, INSERT ON tenants TO casemgr_app",
    "GRANT SELECT, INSERT, UPDATE ON users TO casemgr_app",
    "GRANT SELECT, INSERT, DELETE ON memberships TO casemgr_app",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON sessions TO casemgr_app",
    "GRANT SELECT, INSERT, UPDATE ON api_tokens TO casemgr_app",
    "GRANT SELECT, INSERT ON audit_log TO casemgr_app",
    """
    INSERT INTO tenants (slug, name) VALUES
        ('detect-respond', 'Detect and Respond'),
        ('insider-threat', 'Insider Threat')
    """,
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE audit_log, api_tokens, sessions, memberships, users, tenants")
    op.execute("DROP FUNCTION reject_change()")
