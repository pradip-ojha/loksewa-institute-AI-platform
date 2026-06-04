import asyncio
import sys
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

# add backend/ to sys.path so app imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.core.config import settings
from app.core.database import Base

# import all models here to register them with Base.metadata
from app.modules.users.models import User  # noqa: F401
from app.modules.syllabus.models import SyllabusItem  # noqa: F401
from app.modules.files.models import File  # noqa: F401
from app.modules.jobs.models import ProcessingJob  # noqa: F401
from app.modules.ai_audit.models import AIRequest, AIOutput  # noqa: F401
from app.modules.knowledge.models import KnowledgeDocument, KnowledgeChunk  # noqa: F401
from app.modules.mcq.models import MCQDocument, MCQReviewBatch, MCQQuestion, MCQRejectionFeedback  # noqa: F401
from app.modules.skill_layer.models import AgentCoreSkill, AgentSkillVersion  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = create_async_engine(settings.DATABASE_URL)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
