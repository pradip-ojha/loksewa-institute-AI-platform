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
    # Borrow-per-use (manage_session=False): the agent self-manages short-lived sessions
    # per phase (LOAD/clean/save), so a single held session would just sit IDLE for the
    # whole multi-minute chunk+embed+Pinecone run and risk a server-side drop. Take none.
    async def work() -> None:
        from app.ai.agents.knowledge_processing_agent import KnowledgeProcessingAgent

        agent = KnowledgeProcessingAgent()
        await agent.process(document_id=document_id, job_id=job_id)

    try:
        run_task(work, job_id=job_id, task=self, manage_session=False)
    except Exception as exc:
        logger.exception("process_knowledge_document task failed: %s", exc)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
