import os

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import text
from sqlalchemy.pool import NullPool
from .config import settings
from .logger import get_logger

_use_null_pool = bool(os.getenv("PYTEST_CURRENT_TEST")) or (settings.app_env or "").strip().lower() in {"test", "pytest"}
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    poolclass=NullPool if _use_null_pool else None,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
log = get_logger("db")


async def _has_any_tables(conn) -> bool:
    res = await conn.execute(
        text(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema='public' AND table_type='BASE TABLE'
            LIMIT 1
            """
        )
    )
    return res.first() is not None


async def _has_alembic_version(conn) -> bool:
    res = await conn.execute(
        text(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema='public'
              AND table_type='BASE TABLE'
              AND table_name='alembic_version'
            LIMIT 1
            """
        )
    )
    return res.first() is not None


async def init_db():
    async with engine.begin() as conn:
        if not await _has_any_tables(conn):
            msg = "db schema not initialized; run `alembic upgrade head`"
            if (settings.app_env or "").lower() == "dev":
                log.warning(msg)
                return
            raise RuntimeError(msg)

        if not await _has_alembic_version(conn):
            msg = (
                "db has tables but alembic is not initialized; run `alembic stamp head` "
                "(if schema already matches) or `alembic upgrade head`"
            )
            if (settings.app_env or "").lower() == "dev":
                log.warning(msg)
                return
            raise RuntimeError(msg)


async def get_session():
    async with SessionLocal() as session:
        yield session
