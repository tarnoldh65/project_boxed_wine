import secrets

from authlib.integrations.base_client import OAuthError
from fastapi.responses import RedirectResponse
from httpx2 import AsyncClient
from pytest import MonkeyPatch
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.requests import Request

from app import idp
from app.auth import SESSION_COOKIE
from tests.conftest import MakeUser, audit_actions

ADMIN_PERMISSIONS = ["cases:read", "cases:write", "config:manage", "tokens:manage"]


def identity(subject: str, groups: list[str]) -> idp.Identity:
    return idp.Identity(
        subject=subject,
        username="jdoe",
        email="jdoe@example.test",
        display_name="J Doe",
        groups=groups,
        refresh_token="refresh-1",
        id_token="id-token-1",
    )


def fake_login(monkeypatch: MonkeyPatch, result: idp.Identity | Exception) -> None:
    async def complete_login(request: Request) -> idp.Identity:
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(idp, "complete_login", complete_login)


async def sign_in(client: AsyncClient, monkeypatch: MonkeyPatch, who: idp.Identity) -> str:
    fake_login(monkeypatch, who)
    response = await client.get("/api/auth/callback")
    assert response.status_code == 303
    return response.cookies[SESSION_COOKIE]


async def session_count(owner: AsyncEngine, user_id: object) -> int:
    async with owner.connect() as conn:
        count = await conn.scalar(
            text("SELECT count(*) FROM sessions WHERE user_id = :id"), {"id": user_id}
        )
        return count or 0


async def test_login_redirects_to_idp(client: AsyncClient, monkeypatch: MonkeyPatch) -> None:
    async def login_redirect(request: Request) -> RedirectResponse:
        return RedirectResponse("https://idp.test/auth")

    monkeypatch.setattr(idp, "login_redirect", login_redirect)
    response = await client.get("/api/auth/login")
    assert response.status_code == 307
    assert response.headers["location"] == "https://idp.test/auth"


async def test_callback_creates_session_and_grants_from_groups(
    client: AsyncClient, monkeypatch: MonkeyPatch, owner: AsyncEngine
) -> None:
    groups = [
        "casemgr-detect-respond-admin",
        "casemgr-insider-threat-analyst",
        "casemgr-insider-threat-restricted_case",
        "casemgr-platform_admin",
        "casemgr-unknown-tenant-analyst",
        "casemgr-detect-respond-wizard",
        "unrelated-group",
    ]
    fake_login(monkeypatch, identity(secrets.token_hex(8), groups))

    response = await client.get("/api/auth/callback")

    assert response.status_code == 303
    assert response.headers["location"] == "http://localhost:8080/"
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=strict" in cookie

    client.cookies.set(SESSION_COOKIE, response.cookies[SESSION_COOKIE])
    me = (await client.get("/api/me")).json()
    assert me["display_name"] == "J Doe"
    assert me["is_platform_admin"] is True
    assert me["tenants"] == [
        {"slug": "detect-respond", "name": "Detect and Respond", "permissions": ADMIN_PERMISSIONS},
        {
            "slug": "insider-threat",
            "name": "Insider Threat",
            "permissions": ["cases:read", "cases:write", "restricted_case"],
        },
    ]
    assert await audit_actions(owner, f"user:{me['id']}") == ["grants.synced", "auth.login"]


async def test_login_replaces_grants_with_current_groups(
    client: AsyncClient, monkeypatch: MonkeyPatch
) -> None:
    subject = secrets.token_hex(8)
    await sign_in(client, monkeypatch, identity(subject, ["casemgr-detect-respond-admin"]))
    token = await sign_in(client, monkeypatch, identity(subject, ["casemgr-insider-threat-legal"]))

    client.cookies.set(SESSION_COOKIE, token)
    me = (await client.get("/api/me")).json()
    assert me["is_platform_admin"] is False
    assert [t["slug"] for t in me["tenants"]] == ["insider-threat"]


async def test_failed_login_is_rejected_and_audited(
    client: AsyncClient, monkeypatch: MonkeyPatch, owner: AsyncEngine
) -> None:
    fake_login(monkeypatch, OAuthError(error="mismatching_state"))

    response = await client.get("/api/auth/callback")

    assert response.status_code == 400
    async with owner.connect() as conn:
        last = (
            await conn.execute(
                text("SELECT actor, action, detail FROM audit_log ORDER BY id DESC LIMIT 1")
            )
        ).one()
    assert tuple(last) == ("anonymous", "auth.login_failed", {"error": "mismatching_state"})


async def test_unknown_session_is_rejected(client: AsyncClient) -> None:
    client.cookies.set(SESSION_COOKIE, "not-a-session")
    assert (await client.get("/api/me")).status_code == 401


async def test_idle_session_expires(make_user: MakeUser, owner: AsyncEngine) -> None:
    user = await make_user({"detect-respond": ["analyst"]})
    async with owner.begin() as conn:
        await conn.execute(
            text(
                "UPDATE sessions SET last_seen_at = now() - interval '31 minutes' "
                "WHERE user_id = :id"
            ),
            {"id": user.id},
        )

    assert (await user.client.get("/api/me")).status_code == 401
    assert await session_count(owner, user.id) == 0


async def test_session_expires_at_max_lifetime(make_user: MakeUser, owner: AsyncEngine) -> None:
    user = await make_user({"detect-respond": ["analyst"]})
    async with owner.begin() as conn:
        await conn.execute(
            text(
                "UPDATE sessions SET created_at = now() - interval '13 hours' WHERE user_id = :id"
            ),
            {"id": user.id},
        )

    assert (await user.client.get("/api/me")).status_code == 401
    assert await session_count(owner, user.id) == 0


async def test_activity_keeps_session_alive(make_user: MakeUser, owner: AsyncEngine) -> None:
    user = await make_user({"detect-respond": ["analyst"]})
    async with owner.begin() as conn:
        await conn.execute(
            text(
                "UPDATE sessions SET last_seen_at = now() - interval '29 minutes' "
                "WHERE user_id = :id"
            ),
            {"id": user.id},
        )

    assert (await user.client.get("/api/me")).status_code == 200
    async with owner.connect() as conn:
        idle = await conn.scalar(
            text(
                "SELECT now() - last_seen_at < interval '1 minute' "
                "FROM sessions WHERE user_id = :id"
            ),
            {"id": user.id},
        )
    assert idle is True


async def make_stale(owner: AsyncEngine, user_id: object) -> None:
    async with owner.begin() as conn:
        await conn.execute(
            text(
                "UPDATE sessions SET synced_at = now() - interval '6 minutes' WHERE user_id = :id"
            ),
            {"id": user_id},
        )


async def test_stale_session_resyncs_groups(
    make_user: MakeUser, owner: AsyncEngine, monkeypatch: MonkeyPatch
) -> None:
    user = await make_user({"detect-respond": ["admin"]})
    await make_stale(owner, user.id)

    async def refresh(refresh_token: str) -> tuple[list[str], str]:
        assert refresh_token == "refresh-1"
        return ["casemgr-insider-threat-auditor"], "refresh-2"

    monkeypatch.setattr(idp, "refresh", refresh)

    me = (await user.client.get("/api/me")).json()

    assert [t["slug"] for t in me["tenants"]] == ["insider-threat"]
    async with owner.connect() as conn:
        stored = await conn.scalar(
            text("SELECT refresh_token FROM sessions WHERE user_id = :id"), {"id": user.id}
        )
    assert stored == "refresh-2"
    assert "grants.synced" in await audit_actions(owner, f"user:{user.id}")


async def test_failed_resync_ends_session(
    make_user: MakeUser, owner: AsyncEngine, monkeypatch: MonkeyPatch
) -> None:
    user = await make_user({"detect-respond": ["admin"]})
    await make_stale(owner, user.id)

    async def refresh(refresh_token: str) -> tuple[list[str], str]:
        raise OAuthError(error="invalid_grant")

    monkeypatch.setattr(idp, "refresh", refresh)

    assert (await user.client.get("/api/me")).status_code == 401
    assert await session_count(owner, user.id) == 0


async def test_logout_ends_session(
    make_user: MakeUser, owner: AsyncEngine, monkeypatch: MonkeyPatch
) -> None:
    user = await make_user({"detect-respond": ["analyst"]})

    async def logout_url(id_token: str | None) -> str:
        return f"https://idp.test/logout?hint={id_token}"

    monkeypatch.setattr(idp, "logout_url", logout_url)

    response = await user.client.post("/api/auth/logout")

    assert response.status_code == 200
    assert response.json() == {"logout_url": "https://idp.test/logout?hint=id-token-1"}
    assert f'{SESSION_COOKIE}=""' in response.headers["set-cookie"]
    assert await session_count(owner, user.id) == 0
    assert await audit_actions(owner, f"user:{user.id}") == ["auth.logout"]


async def test_logout_without_session(client: AsyncClient, monkeypatch: MonkeyPatch) -> None:
    async def logout_url(id_token: str | None) -> str:
        return f"https://idp.test/logout?hint={id_token}"

    monkeypatch.setattr(idp, "logout_url", logout_url)
    response = await client.post("/api/auth/logout")
    assert response.json() == {"logout_url": "https://idp.test/logout?hint=None"}
