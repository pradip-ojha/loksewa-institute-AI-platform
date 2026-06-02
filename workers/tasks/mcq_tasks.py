import asyncio
import logging

from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="workers.tasks.mcq_tasks.extract_mcqs",
    max_retries=2,
    default_retry_delay=30,
)
def extract_mcqs(self, job_id: str, document_id: str) -> None:
    async def _run() -> None:
        from app.core.database import AsyncSessionLocal, engine
        from app.modules.mcq.models import MCQDocument
        from app.ai.agents.mcq_extraction_agent import MCQExtractionAgent
        from sqlalchemy import select
        import uuid

        try:
            async with AsyncSessionLocal() as db:
                doc_r = await db.execute(select(MCQDocument).where(MCQDocument.id == uuid.UUID(document_id)))
                document = doc_r.scalar_one_or_none()
                if not document:
                    raise ValueError(f"MCQDocument {document_id} not found")
                agent = MCQExtractionAgent(db=db, job_id=uuid.UUID(job_id), document=document)
                await agent.process()
        finally:
            await engine.dispose()

    try:
        asyncio.run(_run())
    except Exception as exc:
        logger.exception("extract_mcqs task failed: %s", exc)
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))


@celery_app.task(
    bind=True,
    name="workers.tasks.mcq_tasks.generate_mcqs",
    max_retries=2,
    default_retry_delay=30,
)
def generate_mcqs(self, job_id: str, document_id: str, count: int) -> None:
    async def _run() -> None:
        from app.core.database import AsyncSessionLocal, engine
        from app.modules.mcq.models import MCQDocument
        from app.ai.agents.mcq_extraction_agent import MCQGenerationAgent
        from sqlalchemy import select
        import uuid

        try:
            async with AsyncSessionLocal() as db:
                doc_r = await db.execute(select(MCQDocument).where(MCQDocument.id == uuid.UUID(document_id)))
                document = doc_r.scalar_one_or_none()
                if not document:
                    raise ValueError(f"MCQDocument {document_id} not found")
                agent = MCQGenerationAgent(db=db, job_id=uuid.UUID(job_id), document=document, count=count)
                await agent.process()
        finally:
            await engine.dispose()

    try:
        asyncio.run(_run())
    except Exception as exc:
        logger.exception("generate_mcqs task failed: %s", exc)
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))


@celery_app.task(
    bind=True,
    name="workers.tasks.mcq_tasks.regenerate_rejected_mcqs",
    max_retries=2,
    default_retry_delay=30,
)
def regenerate_rejected_mcqs(self, job_id: str, batch_id: str, rejection_feedback: str) -> None:
    async def _run() -> None:
        from app.core.database import AsyncSessionLocal, engine
        from app.modules.mcq.models import MCQReviewBatch
        from app.ai.agents.mcq_extraction_agent import MCQRegenerationAgent
        from sqlalchemy import select
        import uuid

        try:
            async with AsyncSessionLocal() as db:
                batch_r = await db.execute(select(MCQReviewBatch).where(MCQReviewBatch.id == uuid.UUID(batch_id)))
                batch = batch_r.scalar_one_or_none()
                if not batch:
                    raise ValueError(f"MCQReviewBatch {batch_id} not found")
                agent = MCQRegenerationAgent(db=db, job_id=uuid.UUID(job_id), batch=batch, feedback=rejection_feedback)
                await agent.process()
        finally:
            await engine.dispose()

    try:
        asyncio.run(_run())
    except Exception as exc:
        logger.exception("regenerate_rejected_mcqs task failed: %s", exc)
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))
