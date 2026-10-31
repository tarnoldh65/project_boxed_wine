import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import NullPool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations() -> None:
    # Migrations run as the schema owner, not the application role.
    engine = create_async_engine(os.environ["CASEMGR_MIGRATION_DATABASE_URL"], poolclass=NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
        await connection.commit()
    await engine.dispose()


asyncio.run(run_migrations())
