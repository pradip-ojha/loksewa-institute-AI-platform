import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

logger = logging.getLogger(__name__)

# Pool sizing (spec §7 #5): the API process and each Celery worker get their OWN pool.
# Sized via DB_POOL_SIZE/DB_MAX_OVERFLOW so total connections —
# (FastAPI + worker×concurrency + beat) — stay under Azure PG max_connections. Defaults
# (5 + 10 overflow = 15/process) comfortably cover the in-task asyncio concurrency
# (parallel page extraction / skill-gen check out a few short-lived sessions at once).
# pool_pre_ping validates a connection on checkout; pool_recycle guards against the
# server dropping idle connections (surfaces otherwise as "connection was closed"
# mid-query). Raise the env values on a larger PG tier or higher worker concurrency.
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_recycle=1800,   # recycle connections older than 30 min (idle cutoff)
    pool_timeout=30,     # fail fast instead of hanging when the pool is exhausted
    # asyncpg: per-statement timeout prevents a hung query from blocking a worker
    # forever; application_name makes sessions identifiable in pg_stat_activity.
    connect_args={
        "command_timeout": 60,
        "server_settings": {"application_name": "neurafix"},
    },
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            try:
                await session.rollback()
            except Exception:
                # Distinct from the original failure: surfacing this separately
                # so a broken-connection rollback can't mask the real error.
                logger.exception("Session rollback failed while handling a request error")
            raise
        finally:
            await session.close()


@asynccontextmanager
async def transaction(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """Wrap a multi-statement write so it commits atomically or not at all.

    Use for any workflow that touches more than one row/table and must not leave
    partial state (e.g. create batch → insert questions → update document status):

        async with transaction(db):
            db.add(batch)
            ...

    On any exception the whole block is rolled back and the error re-raised.
    """
    try:
        async with session.begin():
            yield session
    except Exception:
        # session.begin() already rolled back; re-raise for the caller to handle.
        raise
