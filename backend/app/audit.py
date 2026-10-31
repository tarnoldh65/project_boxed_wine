import hashlib
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEntry

GENESIS = "0" * 64
# Serializes writers so each entry chains to the one before it.
CHAIN_LOCK = 1_000_001

# Every entry is also written to stdout as JSON for collection by the SIEM.
log = logging.getLogger("casemgr.audit")
log.addHandler(logging.StreamHandler(sys.stdout))
log.setLevel(logging.INFO)
log.propagate = False


def _payload(entry: AuditEntry) -> dict[str, Any]:
    return {
        "at": entry.at.isoformat(),
        "actor": entry.actor,
        "tenant_id": str(entry.tenant_id) if entry.tenant_id else None,
        "action": entry.action,
        "target": entry.target,
        "detail": entry.detail,
    }


def entry_hash(entry: AuditEntry) -> str:
    canonical = json.dumps(_payload(entry), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((entry.prev_hash + canonical).encode()).hexdigest()


async def record(
    db: AsyncSession,
    actor: str,
    action: str,
    target: str,
    tenant_id: UUID | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    await db.execute(select(func.pg_advisory_xact_lock(CHAIN_LOCK)))
    prev = await db.scalar(select(AuditEntry.hash).order_by(AuditEntry.id.desc()).limit(1))
    entry = AuditEntry(
        at=datetime.now(UTC),
        actor=actor,
        tenant_id=tenant_id,
        action=action,
        target=target,
        detail=detail or {},
        prev_hash=prev or GENESIS,
    )
    entry.hash = entry_hash(entry)
    db.add(entry)
    await db.flush()
    log.info(json.dumps({"audit": _payload(entry) | {"id": entry.id, "hash": entry.hash}}))


async def verify(db: AsyncSession) -> int | None:
    """Return the id of the first entry that breaks the hash chain, or None if intact."""
    prev = GENESIS
    async for entry in await db.stream_scalars(select(AuditEntry).order_by(AuditEntry.id)):
        if entry.prev_hash != prev or entry.hash != entry_hash(entry):
            return entry.id
        prev = entry.hash
    return None
