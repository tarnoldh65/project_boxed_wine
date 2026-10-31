import os
from collections.abc import AsyncIterator, Iterator

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from testcontainers.community.postgres import PostgresContainer


@pytest.fixture(scope="session", autouse=True)
def database() -> Iterator[None]:
    with PostgresContainer("postgres:18.6", driver="asyncpg") as pg:
        os.environ["CASEMGR_DATABASE_URL"] = pg.get_connection_url()
        command.upgrade(Config("alembic.ini", attributes={"configure_logger": False}), "head")
        yield


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
