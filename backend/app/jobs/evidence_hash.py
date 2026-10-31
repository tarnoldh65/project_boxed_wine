"""Hash newly stored evidence by streaming it from object storage."""

import asyncio

from sqlalchemy import select

from app import evidence, storage
from app.db import SessionLocal, set_tenant
from app.evidence import EvidenceStatus
from app.models import Evidence, Tenant

ACTOR = "system:evidence_hash"


async def run() -> int:
    async with SessionLocal() as db:
        tenants = (await db.scalars(select(Tenant).order_by(Tenant.slug))).all()
    for tenant in tenants:
        async with SessionLocal() as db:
            await set_tenant(db, tenant.id)
            pending = (
                await db.scalars(
                    select(Evidence.id).where(Evidence.status == EvidenceStatus.STORED)
                )
            ).all()
        for evidence_id in pending:
            # One transaction per item, so a large object never holds others back.
            async with SessionLocal() as db:
                await set_tenant(db, tenant.id)
                item = await db.get_one(Evidence, evidence_id)
                await evidence.hash_stored(db, item, ACTOR)
                await db.commit()
            print(f"{tenant.slug}: hashed evidence {evidence_id}")
    return 0


async def create_bucket() -> int:
    await asyncio.to_thread(storage.ensure_bucket)
    return 0
