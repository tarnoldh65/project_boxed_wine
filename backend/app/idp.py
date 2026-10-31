"""OpenID Connect client for the identity provider. MFA is enforced by the IdP."""

from dataclasses import dataclass
from urllib.parse import urlencode

from authlib.integrations.starlette_client import OAuth
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings

oauth = OAuth()
client = oauth.register(
    "idp",
    server_metadata_url=settings.oidc_metadata_url,
    client_id=settings.oidc_client_id,
    client_secret=settings.oidc_client_secret,
    client_kwargs={"scope": "openid profile email", "code_challenge_method": "S256"},
)


@dataclass(frozen=True)
class Identity:
    subject: str
    username: str
    email: str | None
    display_name: str
    groups: list[str]
    refresh_token: str
    id_token: str


async def login_redirect(request: Request) -> Response:
    return await client.authorize_redirect(request, f"{settings.public_url}/api/auth/callback")


async def complete_login(request: Request) -> Identity:
    token = await client.authorize_access_token(request)
    claims = token["userinfo"]
    return Identity(
        subject=claims["sub"],
        username=claims["preferred_username"],
        email=claims.get("email"),
        display_name=claims.get("name") or claims["preferred_username"],
        groups=claims.get("groups", []),
        refresh_token=token["refresh_token"],
        id_token=token["id_token"],
    )


async def refresh(refresh_token: str) -> tuple[list[str], str]:
    """Return the user's current groups and a new refresh token."""
    token = await client.fetch_access_token(grant_type="refresh_token", refresh_token=refresh_token)
    userinfo = await client.userinfo(token=token)
    return userinfo.get("groups", []), token["refresh_token"]


async def logout_url(id_token: str | None) -> str:
    """IdP logout URL; with the ID token as a hint the IdP skips its confirmation page."""
    metadata = await client.load_server_metadata()
    params = {"client_id": settings.oidc_client_id, "post_logout_redirect_uri": settings.public_url}
    if id_token:
        params["id_token_hint"] = id_token
    query = urlencode(params)
    return f"{metadata['end_session_endpoint']}?{query}"
