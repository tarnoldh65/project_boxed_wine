import json
from pathlib import Path

import pytest

from app.main import app

SCHEMA = Path("openapi.json")


def test_openapi_schema_is_committed() -> None:
    """The frontend API client is generated from openapi.json, so it must match the app."""
    current = json.dumps(app.openapi(), indent=2) + "\n"
    if not SCHEMA.exists() or SCHEMA.read_text() != current:
        SCHEMA.write_text(current, newline="\n")
        pytest.fail("openapi.json was out of date and has been regenerated; commit it")
