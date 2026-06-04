import logging

from workers.celery_app import celery_app
from workers.runtime import run_task

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="workers.tasks.knowledge_tasks.process_knowledge_document",
    max_retries=3,
    default_retry_delay=60,
)
def process_knowledge_document(self, job_id: str, document_id: str) -> None:
    async def work(db) -> None:
        from app.ai.agents.knowledge_processing_agent import KnowledgeProcessingAgent

        agent = KnowledgeProcessingAgent()
        await agent.process(document_id=document_id, job_id=job_id, db=db)

    try:
        run_task(work, job_id=job_id, task=self)
    except Exception as exc:
        logger.exception("process_knowledge_document task failed: %s", exc)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
