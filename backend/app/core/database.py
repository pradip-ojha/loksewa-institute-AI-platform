import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

logger = logging.getLogger(__name__)

# Pool sizing (spec §7 #5): the API process and EACH Celery worker process get their OWN
# pool of (DB_POOL_SIZE + DB_MAX_OVERFLOW) connections. Defaults = 8 + 12 = 20/process.
#
# Per-task peak (worker): a long pipeline fans out at Semaphore(6) — each parallel branch
# opens one short-lived session — plus the heartbeat-loop session and the brief mark/done
# + self-managed-audit sessions, so a single task peaks at roughly 6–8 concurrent checkouts.
# Under prefork --concurrency=C, the worker's real ceiling is C × (that peak); 20/process
# comfortably covers C=2 (≈16 < 20). The audit write now runs on its OWN short session
# (ai/providers/*._audit) and connections are no longer held idle across AI calls except a
# brief per-call active-skill read, so peak checkouts stay bounded.
#
# TOTAL across the deployment = API pool + (worker_processes × concurrency-shaped pool) +
# beat — keep this under the Azure PG tier's max_connections. Raise the env values on a
# larger tier or higher concurrency; lower them for a small tier.
# pool_pre_ping validates a connection on checkout; pool_recycle guards against the server
# dropping idle connections (otherwise surfaces as "connection was closed" mid-query).
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
