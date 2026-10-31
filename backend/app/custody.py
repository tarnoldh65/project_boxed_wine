"""Chain of custody: an append-only, hash-chained history for each evidence item."""

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CustodyEntry, Evidence

GENESIS = "0" * 64


class CustodyAction(StrEnum):
    COLLECTED = "collected"
    UPLOADED = "uploaded"
    REGISTERED = "registered"  # a reference to evidence held elsewhere
    HASHED = "hashed"
    ACCESSED = "accessed"
    VERIFIED = "verified"
    MISMATCH = "mismatch"
    TRANSFERRED = "transferred"
    DELETED = "deleted"


class CustodyEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    at: datetime
    actor: str
    action: CustodyAction
    detail: dict[str, Any]
    hash: str


def entry_hash(entry: CustodyEntry) -> str:
    canonical = json.dumps(
        {
            "evidence_id": str(entry.evidence_id),
            "at": entry.at.isoformat(),
            "actor": entry.actor,
            "action": entry.action,
            "detail": entry.detail,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256((entry.prev_hash + canonical).encode()).hexdigest()


async def record(
    db: AsyncSession, evidence: Evidence, actor: str, action: CustodyAction, **detail: Any
) -> None:
    # Serialize writers per item so each entry chains to the one before it.
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(str(evidence.id), 0))))
    prev = await db.scalar(
        select(CustodyEntry.hash)
        .where(CustodyEntry.evidence_id == evidence.id)
        .order_by(CustodyEntry.id.desc())
        .limit(1)
    )
    entry = CustodyEntry(
        tenant_id=evidence.tenant_id,
        evidence_id=evidence.id,
        at=datetime.now(UTC),
        actor=actor,
        action=action,
        detail=detail,
        prev_hash=prev or GENESIS,
    )
    entry.hash = entry_hash(entry)
    db.add(entry)
    await db.flush()


async def entries(db: AsyncSession, evidence: Evidence) -> Sequence[CustodyEntry]:
    query = (
        select(CustodyEntry)
        .where(CustodyEntry.evidence_id == evidence.id)
        .order_by(CustodyEntry.id)
    )
    return (await db.scalars(query)).all()


def chain_intact(log: Sequence[CustodyEntry]) -> bool:
    prev = GENESIS
    for entry in log:
        if entry.prev_hash != prev or entry.hash != entry_hash(entry):
            return False
        prev = entry.hash
    return True
