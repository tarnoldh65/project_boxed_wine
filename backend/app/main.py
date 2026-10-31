from fastapi import FastAPI
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.db import engine
from app.routers import auth, me, tenants, tokens

app = FastAPI(title="Case Manager")
# Holds OIDC state, nonce, and PKCE verifier between login and callback only.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="casemgr_oidc",
    max_age=600,
    same_site="lax",
    https_only=True,
)
for module in (auth, me, tenants, tokens):
    app.include_router(module.router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ready"}
