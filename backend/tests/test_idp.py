from typing import Any

from pytest import MonkeyPatch
from starlette.requests import Request
from starlette.responses import RedirectResponse

from app import idp

REQUEST = Request({"type": "http"})


async def test_login_redirect_uses_public_callback(monkeypatch: MonkeyPatch) -> None:
    calls: list[str] = []

    async def authorize_redirect(request: Request, redirect_uri: str) -> RedirectResponse:
        calls.append(redirect_uri)
        return RedirectResponse("https://idp.test/auth")

    monkeypatch.setattr(idp.client, "authorize_redirect", authorize_redirect)
    await idp.login_redirect(REQUEST)
    assert calls == ["http://localhost:8080/api/auth/callback"]


async def test_complete_login_maps_claims(monkeypatch: MonkeyPatch) -> None:
    async def authorize_access_token(request: Request) -> dict[str, Any]:
        return {
            "refresh_token": "refresh-1",
            "id_token": "id-token-1",
            "userinfo": {"sub": "abc", "preferred_username": "jdoe", "groups": ["g1"]},
        }

    monkeypatch.setattr(idp.client, "authorize_access_token", authorize_access_token)
    assert await idp.complete_login(REQUEST) == idp.Identity(
        subject="abc",
        username="jdoe",
        email=None,
        display_name="jdoe",
        groups=["g1"],
        refresh_token="refresh-1",
        id_token="id-token-1",
    )


async def test_refresh_returns_current_groups(monkeypatch: MonkeyPatch) -> None:
    async def fetch_access_token(**kwargs: str) -> dict[str, str]:
        assert kwargs == {"grant_type": "refresh_token", "refresh_token": "refresh-1"}
        return {"access_token": "access", "refresh_token": "refresh-2"}

    async def userinfo(token: dict[str, str]) -> dict[str, Any]:
        assert token["access_token"] == "access"
        return {"sub": "abc", "groups": ["g2"]}

    monkeypatch.setattr(idp.client, "fetch_access_token", fetch_access_token)
    monkeypatch.setattr(idp.client, "userinfo", userinfo)
    assert await idp.refresh("refresh-1") == (["g2"], "refresh-2")


async def test_logout_url_returns_to_app(monkeypatch: MonkeyPatch) -> None:
    async def load_server_metadata() -> dict[str, str]:
        return {"end_session_endpoint": "https://idp.test/logout"}

    monkeypatch.setattr(idp.client, "load_server_metadata", load_server_metadata)
    base = (
        "https://idp.test/logout?client_id=casemgr"
        "&post_logout_redirect_uri=http%3A%2F%2Flocalhost%3A8080"
    )
    assert await idp.logout_url(None) == base
    assert await idp.logout_url("id-token-1") == f"{base}&id_token_hint=id-token-1"
