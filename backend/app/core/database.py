import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

logger = logging.getLogger(__name__)

# Pool sizing note: the API process and each Celery worker get their own pool.
# Workers run with --pool=solo (one task at a time), so concurrency per worker is
# low; pool_size=10 is comfortable. pool_recycle guards against Neon dropping
# idle connections (it closes them server-side after a few minutes, which would
# otherwise surface as "connection was closed" mid-query). Revisit pool_size if
# we ever move workers to the prefork pool with concurrency > 1.
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=1800,   # recycle connections older than 30 min (Neon idle cutoff)
    pool_timeout=30,     # fail fast instead of hanging when the pool is exhausted
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
