import asyncio
import logging

from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="workers.tasks.knowledge_tasks.process_knowledge_document",
    max_retries=3,
    default_retry_delay=60,
)
def process_knowledge_document(self, job_id: str, document_id: str) -> None:
    async def _run() -> None:
        from app.core.database import AsyncSessionLocal, engine
        from app.ai.agents.knowledge_processing_agent import KnowledgeProcessingAgent

        try:
            async with AsyncSessionLocal() as db:
                agent = KnowledgeProcessingAgent()
                await agent.process(document_id=document_id, job_id=job_id, db=db)
        finally:
            # Must dispose before asyncio.run() closes the loop.
            # The global engine pool holds connections bound to this loop —
            # if they survive into the next asyncio.run() call they trigger
            # "RuntimeError: Event loop is closed" on every retry.
            await engine.dispose()

    try:
        asyncio.run(_run())
    except Exception as exc:
        logger.exception("process_knowledge_document task failed: %s", exc)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
