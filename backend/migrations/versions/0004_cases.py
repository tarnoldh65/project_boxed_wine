"""cases, alerts, assets

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-31
"""

from alembic import op

from app.rls import tenant_rls

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None

UPGRADE = [
    """
    CREATE TABLE case_numbers (
        tenant_id uuid PRIMARY KEY REFERENCES tenants,
        last integer NOT NULL
    )
    """,
    """
    CREATE TABLE cases (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenants,
        number integer NOT NULL,
        title text NOT NULL,
        description text NOT NULL,
        state text NOT NULL,
        category text NOT NULL,
        impact text NOT NULL,
        urgency text NOT NULL,
        severity text NOT NULL,
        priority text NOT NULL,
        override_justification text,
        team text,
        owner_id uuid REFERENCES users,
        source text NOT NULL,
        tlp text NOT NULL,
        pap text NOT NULL,
        restricted boolean NOT NULL,
        regulated_data text[] NOT NULL,
        data_regions text[] NOT NULL,
        business_units text[] NOT NULL,
        attack_techniques text[] NOT NULL,
        custom jsonb NOT NULL,
        resolution text,
        merged_into_id uuid REFERENCES cases,
        detected_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        closed_at timestamptz,
        UNIQUE (tenant_id, number)
    )
    """,
    """
    CREATE TABLE alerts (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenants,
        case_id uuid NOT NULL REFERENCES cases,
        source text NOT NULL,
        external_id text,
        group_key text,
        title text NOT NULL,
        payload jsonb NOT NULL,
        received_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, source, external_id)
    )
    """,
    """
    CREATE TABLE assets (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenants,
        kind text NOT NULL,
        name text NOT NULL,
        external_id text,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, kind, name)
    )
    """,
    """
    CREATE TABLE case_assets (
        case_id uuid NOT NULL REFERENCES cases,
        asset_id uuid NOT NULL REFERENCES assets,
        tenant_id uuid NOT NULL REFERENCES tenants,
        PRIMARY KEY (case_id, asset_id)
    )
    """,
    "GRANT SELECT, INSERT, UPDATE ON case_numbers TO casemgr_app",
    "GRANT SELECT, INSERT, UPDATE ON cases TO casemgr_app",
    "GRANT SELECT, INSERT, UPDATE ON alerts TO casemgr_app",
    "GRANT SELECT, INSERT, UPDATE ON assets TO casemgr_app",
    "GRANT SELECT, INSERT, DELETE ON case_assets TO casemgr_app",
    "CREATE INDEX cases_tenant_created_idx ON cases (tenant_id, created_at DESC)",
    "CREATE INDEX alerts_case_idx ON alerts (case_id)",
    "CREATE INDEX alerts_group_idx ON alerts (tenant_id, group_key)",
    "CREATE INDEX case_assets_asset_idx ON case_assets (asset_id)",
]

TENANT_TABLES = ["case_numbers", "cases", "alerts", "assets", "case_assets"]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)
    for table in TENANT_TABLES:
        for statement in tenant_rls(table):
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE case_assets, assets, alerts, cases, case_numbers")
