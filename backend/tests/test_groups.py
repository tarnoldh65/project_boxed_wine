from uuid import uuid4

import pytest

from app.auth import parse_groups, permissions_for

DR, IT = uuid4(), uuid4()
TENANTS = {"detect-respond": DR, "insider-threat": IT}


@pytest.mark.parametrize(
    ("groups", "expected"),
    [
        ([], set()),
        (["casemgr-detect-respond-analyst"], {(DR, "analyst")}),
        (["casemgr-insider-threat-ir_lead"], {(IT, "ir_lead")}),
        (["casemgr-insider-threat-malware:read"], {(IT, "malware:read")}),
        (["casemgr-detect-respond-restricted_case"], {(DR, "restricted_case")}),
        (
            ["casemgr-detect-respond-admin", "casemgr-detect-respond-auditor"],
            {(DR, "admin"), (DR, "auditor")},
        ),
        (["casemgr-platform_admin"], set()),
        (["casemgr-other-tenant-analyst"], set()),
        (["casemgr-detect-respond-superuser"], set()),
        (["casemgr-detect-respond"], set()),
        (["detect-respond-analyst"], set()),
        (["Casemgr-detect-respond-analyst"], set()),
        (["casemgr--analyst"], set()),
    ],
)
def test_parse_groups(groups: list[str], expected: set[tuple[object, str]]) -> None:
    assert parse_groups(groups, TENANTS) == expected


@pytest.mark.parametrize(
    ("grants", "expected"),
    [
        ([], set()),
        (["executive"], {"cases:read"}),
        (["analyst"], {"cases:read", "cases:write"}),
        (["admin"], {"cases:read", "cases:write", "config:manage", "tokens:manage"}),
        (["legal", "malware:read"], {"cases:read", "malware:read"}),
        (["cases:read"], {"cases:read"}),
    ],
)
def test_permissions_for(grants: list[str], expected: set[str]) -> None:
    assert permissions_for(grants) == expected
