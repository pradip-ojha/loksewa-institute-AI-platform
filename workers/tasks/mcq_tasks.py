import logging
import uuid

from workers.celery_app import celery_app
from workers.runtime import run_task

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="workers.tasks.mcq_tasks.extract_mcqs",
    max_retries=2,
    default_retry_delay=30,
)
def extract_mcqs(self, job_id: str, document_id: str) -> None:
    # Borrow-per-use sessions (manage_session=False): the agent self-manages short-lived
    # sessions per phase so no pooled connection is held idle across the multi-minute AI
    # call (CLAUDE.md §18). `work()` takes NO long-lived session.
    async def work() -> None:
        from app.core.database import AsyncSessionLocal
        from app.modules.mcq.models import MCQDocument
        from app.ai.agents.mcq_extraction_agent import MCQExtractionAgent
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            document = (await db.execute(
                select(MCQDocument).where(MCQDocument.id == uuid.UUID(document_id))
            )).scalar_one_or_none()
        if not document:
            raise ValueError(f"MCQDocument {document_id} not found")
        await MCQExtractionAgent(job_id=uuid.UUID(job_id), document=document).process()

    try:
        run_task(work, job_id=job_id, task=self, manage_session=False)
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
    # Borrow-per-use sessions (manage_session=False) — see extract_mcqs.
    async def work() -> None:
        from app.core.database import AsyncSessionLocal
        from app.modules.mcq.models import MCQDocument
        from app.ai.agents.mcq_extraction_agent import MCQGenerationAgent
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            document = (await db.execute(
                select(MCQDocument).where(MCQDocument.id == uuid.UUID(document_id))
            )).scalar_one_or_none()
        if not document:
            raise ValueError(f"MCQDocument {document_id} not found")
        await MCQGenerationAgent(job_id=uuid.UUID(job_id), document=document, count=count).process()

    try:
        run_task(work, job_id=job_id, task=self, manage_session=False)
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
    # Borrow-per-use sessions (manage_session=False) — see extract_mcqs.
    async def work() -> None:
        from app.core.database import AsyncSessionLocal
        from app.modules.mcq.models import MCQReviewBatch
        from app.ai.agents.mcq_extraction_agent import MCQRegenerationAgent
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            batch = (await db.execute(
                select(MCQReviewBatch).where(MCQReviewBatch.id == uuid.UUID(batch_id))
            )).scalar_one_or_none()
        if not batch:
            raise ValueError(f"MCQReviewBatch {batch_id} not found")
        await MCQRegenerationAgent(job_id=uuid.UUID(job_id), batch=batch, feedback=rejection_feedback).process()

    try:
        run_task(work, job_id=job_id, task=self, manage_session=False)
    except Exception as exc:
        logger.exception("regenerate_rejected_mcqs task failed: %s", exc)
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))
