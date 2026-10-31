from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import audit
from app.auth import Principal, platform_admin
from app.db import Db
from app.models import Tenant

router = APIRouter(prefix="/api/tenants", tags=["tenants"])

PlatformAdmin = Annotated[Principal, Depends(platform_admin)]


class TenantIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=40)
    name: str = Field(min_length=1, max_length=100)


class TenantOut(BaseModel):
    id: UUID
    slug: str
    name: str


@router.get("")
async def list_tenants(principal: PlatformAdmin, db: Db) -> list[TenantOut]:
    tenants = await db.scalars(select(Tenant).order_by(Tenant.name))
    await audit.record(db, principal.actor, "tenant.list", "tenants")
    return [TenantOut(id=t.id, slug=t.slug, name=t.name) for t in tenants]


@router.post("", status_code=201)
async def create_tenant(body: TenantIn, principal: PlatformAdmin, db: Db) -> TenantOut:
    if await db.scalar(select(Tenant).where(Tenant.slug == body.slug)):
        raise HTTPException(409, "Tenant already exists")
    tenant = Tenant(slug=body.slug, name=body.name)
    db.add(tenant)
    await db.flush()
    await audit.record(db, principal.actor, "tenant.create", f"tenant:{tenant.id}", tenant.id)
    return TenantOut(id=tenant.id, slug=tenant.slug, name=tenant.name)
