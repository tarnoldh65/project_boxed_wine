import os
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from httpx2 import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from testcontainers.community.postgres import PostgresContainer

APP_ROLE = "casemgr_app"
_postgres = PostgresContainer("postgres:18.6", driver="asyncpg")


def pytest_sessionstart(session: pytest.Session) -> None:
    # Runs before collection so test modules can import the app, which reads settings.
    _postgres.start()
    _postgres.exec(
        ["psql", "-U", _postgres.username, "-d", _postgres.dbname, "-c",
         f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_ROLE}'"]
    )  # fmt: skip
    owner_url = _postgres.get_connection_url()
    os.environ.update(
        CASEMGR_MIGRATION_DATABASE_URL=owner_url,
        CASEMGR_DATABASE_URL=owner_url.replace(
            f"{_postgres.username}:{_postgres.password}", f"{APP_ROLE}:{APP_ROLE}"
        ),
        CASEMGR_OIDC_METADATA_URL="https://idp.test/.well-known/openid-configuration",
        CASEMGR_OIDC_CLIENT_SECRET="test-client-secret",
        CASEMGR_SECRET_KEY="test-secret-key",
    )
    command.upgrade(Config("alembic.ini", attributes={"configure_logger": False}), "head")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    _postgres.stop()


@pytest.fixture(scope="session")
async def owner() -> AsyncIterator[AsyncEngine]:
    """Engine connected as the schema owner (a superuser), for setup and inspection."""
    engine = create_async_engine(os.environ["CASEMGR_MIGRATION_DATABASE_URL"])
    yield engine
    await engine.dispose()


def new_client(**kwargs: object) -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test", **kwargs)  # pyright: ignore[reportArgumentType]


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with new_client() as c:
        yield c


@dataclass
class SignedIn:
    id: UUID
    client: AsyncClient


MakeUser = Callable[..., Awaitable[SignedIn]]


@pytest.fixture
async def make_user(owner: AsyncEngine) -> AsyncIterator[MakeUser]:
    """Create a user with grants per tenant slug and return a client signed in as them."""
    from app.auth import SESSION_COOKIE, hash_secret

    clients: list[AsyncClient] = []

    async def make(grants: dict[str, list[str]] | None = None, platform_admin: bool = False):
        token = secrets.token_urlsafe(16)
        async with owner.begin() as conn:
            user_id = await conn.scalar(
                text(
                    "INSERT INTO users (subject, username, display_name, is_platform_admin) "
                    "VALUES (:sub, 'tester', 'Test User', :admin) RETURNING id"
                ),
                {"sub": secrets.token_hex(8), "admin": platform_admin},
            )
            for slug, names in (grants or {}).items():
                for name in names:
                    await conn.execute(
                        text(
                            "INSERT INTO memberships SELECT :user_id, id, :name "
                            "FROM tenants WHERE slug = :slug"
                        ),
                        {"user_id": user_id, "name": name, "slug": slug},
                    )
            await conn.execute(
                text(
                    "INSERT INTO sessions (token_hash, user_id, refresh_token, id_token) "
                    "VALUES (:hash, :user_id, 'refresh-1', 'id-token-1')"
                ),
                {"hash": hash_secret(token), "user_id": user_id},
            )
        client = new_client(cookies={SESSION_COOKIE: token})
        clients.append(client)
        return SignedIn(user_id, client)

    yield make
    for c in clients:
        await c.aclose()


async def audit_actions(owner: AsyncEngine, actor: str) -> list[str]:
    async with owner.connect() as conn:
        rows = await conn.scalars(
            text("SELECT action FROM audit_log WHERE actor = :actor ORDER BY id"), {"actor": actor}
        )
        return list(rows)


async def new_tenant(owner: AsyncEngine) -> str:
    async with owner.begin() as conn:
        slug = await conn.scalar(
            text(
                "INSERT INTO tenants (slug, name) "
                "VALUES ('t-' || substr(md5(random()::text), 1, 8), 'Test') RETURNING slug"
            )
        )
    assert slug is not None
    return slug


@pytest.fixture
async def tenant(owner: AsyncEngine) -> str:
    """A fresh tenant, so tests don't see each other's cases or configuration."""
    return await new_tenant(owner)


MakeToken = Callable[..., Awaitable[str]]


@pytest.fixture
def make_token(owner: AsyncEngine) -> MakeToken:
    """Create an API token in a tenant directly and return its secret."""
    from app.auth import hash_secret

    async def make(slug: str, scopes: list[str]) -> str:
        raw = f"cmt_{secrets.token_urlsafe(16)}"
        async with owner.begin() as conn:
            creator = await conn.scalar(
                text(
                    "INSERT INTO users (subject, username, display_name) "
                    "VALUES (:sub, 'token-owner', 'Token Owner') RETURNING id"
                ),
                {"sub": secrets.token_hex(8)},
            )
            await conn.execute(
                text(
                    "INSERT INTO api_tokens "
                    "(tenant_id, name, prefix, token_hash, scopes, created_by, expires_at) "
                    "SELECT id, 'test', :prefix, :hash, :scopes, :creator, "
                    "now() + interval '1 day' FROM tenants WHERE slug = :slug"
                ),
                {"prefix": raw[:12], "hash": hash_secret(raw), "scopes": scopes,
                 "creator": creator, "slug": slug},
            )  # fmt: skip
        return raw

    return make
