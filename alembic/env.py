import asyncio
from logging.config import fileConfig
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


class AlembicSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(..., alias="DATABASE_URL")


alembic_settings = AlembicSettings()
config.set_main_option("sqlalchemy.url", alembic_settings.database_url)


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=None, literal_binds=True)

    with context.begin_transaction():
        context.run_migrations()

def _run_migrations(connection):
    context.configure(connection=connection, target_metadata=None)

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online():
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
