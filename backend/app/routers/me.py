from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from app.auth import Principal, current_user
from app.db import Db
from app.models import Tenant, User

router = APIRouter(prefix="/api", tags=["me"])


class TenantAccess(BaseModel):
    slug: str
    name: str
    permissions: list[str]


class MeOut(BaseModel):
    id: UUID
    username: str
    display_name: str
    email: str | None
    is_platform_admin: bool
    tenants: list[TenantAccess]


@router.get("/me")
async def me(principal: Annotated[Principal, Depends(current_user)], db: Db) -> MeOut:
    user = await db.get_one(User, principal.user_id)
    tenants = await db.scalars(
        select(Tenant).where(Tenant.id.in_(principal.permissions)).order_by(Tenant.name)
    )
    return MeOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        is_platform_admin=user.is_platform_admin,
        tenants=[
            TenantAccess(slug=t.slug, name=t.name, permissions=sorted(principal.permissions[t.id]))
            for t in tenants
        ],
    )
