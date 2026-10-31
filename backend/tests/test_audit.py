import json
import logging

import pytest
from pytest import MonkeyPatch
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app import audit
from app.db import SessionLocal
from app.jobs import __main__ as jobs


async def record_entry(action: str = "test.event") -> int:
    async with SessionLocal() as db:
        await audit.record(db, "system:test", action, "target", detail={"n": 1})
        await db.commit()
        last = await db.scalar(text("SELECT max(id) FROM audit_log"))
    assert last is not None
    return last


async def test_chain_verifies() -> None:
    await record_entry()
    await record_entry()
    async with SessionLocal() as db:
        assert await audit.verify(db) is None
    assert await jobs.audit_verify() == 0


@pytest.mark.parametrize(
    "statement",
    ["UPDATE audit_log SET actor = 'x'", "DELETE FROM audit_log", "TRUNCATE audit_log"],
)
async def test_app_role_cannot_change_audit_log(statement: str) -> None:
    await record_entry()
    async with SessionLocal() as db:
        with pytest.raises(DBAPIError, match="permission denied"):
            await db.execute(text(statement))


@pytest.mark.parametrize(
    "statement",
    ["UPDATE audit_log SET actor = 'x'", "DELETE FROM audit_log", "TRUNCATE audit_log"],
)
async def test_owner_cannot_change_audit_log(owner: AsyncEngine, statement: str) -> None:
    await record_entry()
    with pytest.raises(DBAPIError, match="append-only"):
        async with owner.begin() as conn:
            await conn.execute(text(statement))


async def set_actor_bypassing_trigger(owner: AsyncEngine, entry_id: int, actor: str) -> None:
    async with owner.begin() as conn:
        await conn.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_append_only"))
        await conn.execute(
            text("UPDATE audit_log SET actor = :actor WHERE id = :id"),
            {"actor": actor, "id": entry_id},
        )
        await conn.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_append_only"))


async def test_tampering_is_detected(owner: AsyncEngine) -> None:
    entry_id = await record_entry()
    await record_entry()
    await set_actor_bypassing_trigger(owner, entry_id, "system:tampered")
    try:
        async with SessionLocal() as db:
            assert await audit.verify(db) == entry_id
        assert await jobs.audit_verify() == 1
    finally:
        await set_actor_bypassing_trigger(owner, entry_id, "system:test")
    async with SessionLocal() as db:
        assert await audit.verify(db) is None


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


async def test_entries_are_written_to_stdout_as_json() -> None:
    capture = Capture()
    audit.log.addHandler(capture)
    try:
        await record_entry("test.stdout")
    finally:
        audit.log.removeHandler(capture)
    event = json.loads(capture.records[0].getMessage())["audit"]
    assert event["action"] == "test.stdout"
    assert event["actor"] == "system:test"
    assert len(event["hash"]) == 64


def test_job_runner_returns_job_exit_code(monkeypatch: MonkeyPatch) -> None:
    async def failing() -> int:
        return 1

    monkeypatch.setitem(jobs.JOBS, "audit_verify", failing)
    assert jobs.main(["audit_verify"]) == 1
