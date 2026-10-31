from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
        await session.commit()


# Function scope commits before the response is sent, so a client never sees
# success for work (or an audit entry) that failed to commit.
Db = Annotated[AsyncSession, Depends(get_db, scope="function")]


async def set_tenant(db: AsyncSession, tenant_id: UUID) -> None:
    """Scope row-level security to one tenant for the rest of the transaction."""
    await db.execute(select(func.set_config("app.tenant_id", str(tenant_id), True)))
