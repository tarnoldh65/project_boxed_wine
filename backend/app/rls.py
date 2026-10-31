TENANT_MATCH = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"


def tenant_rls(table: str) -> list[str]:
    """SQL that restricts a tenant-owned table to rows of the transaction's tenant."""
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY tenant_isolation ON {table} "
        f"USING ({TENANT_MATCH}) WITH CHECK ({TENANT_MATCH})",
    ]
