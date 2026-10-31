import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth import TenantContext, hash_secret, require
from app.db import Db
from app.models import ApiToken

router = APIRouter(prefix="/api/tenants/{slug}/tokens", tags=["tokens"])

TokenScope = Literal["alerts:write", "cases:read", "cases:write"]
TokenManager = Annotated[TenantContext, require("tokens:manage")]
TOKEN_PREFIX = "cmt_"  # noqa: S105 - identifies the token type, not a secret


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[TokenScope] = Field(min_length=1)
    expires_in_days: int = Field(ge=1, le=365)


class TokenOut(BaseModel):
    id: UUID
    name: str
    prefix: str
    scopes: list[str]
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None


class TokenCreated(TokenOut):
    token: str


def _out(token: ApiToken) -> TokenOut:
    return TokenOut(
        id=token.id,
        name=token.name,
        prefix=token.prefix,
        scopes=token.scopes,
        created_at=token.created_at,
        expires_at=token.expires_at,
        revoked_at=token.revoked_at,
    )


@router.get("")
async def list_tokens(ctx: TokenManager, db: Db) -> list[TokenOut]:
    tokens = await db.scalars(
        select(ApiToken)
        .where(ApiToken.tenant_id == ctx.tenant.id)
        .order_by(ApiToken.created_at.desc())
    )
    await ctx.audit(db, "token.list", "tokens")
    return [_out(t) for t in tokens]


@router.post("", status_code=201)
async def create_token(body: TokenIn, ctx: TokenManager, db: Db) -> TokenCreated:
    assert ctx.principal.user_id  # noqa: S101 - tokens:manage is never a token scope
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    token = ApiToken(
        tenant_id=ctx.tenant.id,
        name=body.name,
        prefix=raw[:12],
        token_hash=hash_secret(raw),
        scopes=sorted(set(body.scopes)),
        created_by=ctx.principal.user_id,
        expires_at=datetime.now(UTC) + timedelta(days=body.expires_in_days),
    )
    db.add(token)
    await db.flush()
    await db.refresh(token)
    await ctx.audit(db, "token.create", f"token:{token.id}", scopes=token.scopes)
    return TokenCreated(**_out(token).model_dump(), token=raw)


@router.delete("/{token_id}", status_code=204)
async def revoke_token(token_id: UUID, ctx: TokenManager, db: Db) -> None:
    token = await db.scalar(
        select(ApiToken).where(ApiToken.id == token_id, ApiToken.tenant_id == ctx.tenant.id)
    )
    if token is None:
        raise HTTPException(404, "Token not found")
    token.revoked_at = token.revoked_at or datetime.now(UTC)
    await ctx.audit(db, "token.revoke", f"token:{token.id}")
