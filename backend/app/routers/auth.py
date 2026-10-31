import secrets
from datetime import UTC, datetime

from authlib.integrations.base_client import OAuthError
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import delete, select

from app import audit, idp
from app.auth import SESSION_COOKIE, hash_secret, sync_grants
from app.config import settings
from app.db import Db
from app.models import User, UserSession

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/login")
async def login(request: Request) -> Response:
    return await idp.login_redirect(request)


@router.get("/callback")
async def callback(request: Request, db: Db) -> Response:
    try:
        identity = await idp.complete_login(request)
    except OAuthError as e:
        await audit.record(db, "anonymous", "auth.login_failed", "idp", detail={"error": e.error})
        await db.commit()
        raise HTTPException(400, "Login failed") from e

    user = await db.scalar(select(User).where(User.subject == identity.subject))
    if user is None:
        user = User(subject=identity.subject, username=identity.username, display_name="")
        db.add(user)
    user.username = identity.username
    user.email = identity.email
    user.display_name = identity.display_name
    user.last_login_at = datetime.now(UTC)
    await db.flush()
    await sync_grants(db, user, identity.groups)

    token = secrets.token_urlsafe(32)
    db.add(
        UserSession(
            token_hash=hash_secret(token),
            user_id=user.id,
            refresh_token=identity.refresh_token,
            id_token=identity.id_token,
        )
    )
    await audit.record(db, f"user:{user.id}", "auth.login", f"user:{user.id}")

    response = RedirectResponse(f"{settings.public_url}/", status_code=303)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=True, samesite="strict")
    return response


class LogoutOut(BaseModel):
    logout_url: str


@router.post("/logout")
async def logout(request: Request, response: Response, db: Db) -> LogoutOut:
    session = None
    if raw := request.cookies.get(SESSION_COOKIE):
        session = await db.scalar(
            delete(UserSession)
            .where(UserSession.token_hash == hash_secret(raw))
            .returning(UserSession)
        )
        if session is not None:
            await audit.record(
                db, f"user:{session.user_id}", "auth.logout", f"user:{session.user_id}"
            )
    response.delete_cookie(SESSION_COOKIE, httponly=True, secure=True, samesite="strict")
    return LogoutOut(logout_url=await idp.logout_url(session.id_token if session else None))
