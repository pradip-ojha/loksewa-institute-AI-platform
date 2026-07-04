import logging

from workers.celery_app import celery_app
from workers.runtime import run_task

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="workers.tasks.syllabus_tasks.extract_syllabus",
    max_retries=3,
    default_retry_delay=60,
)
def extract_syllabus(self, job_id: str, exam_id: str, file_id: str) -> None:
    # Borrow-per-use (manage_session=False): the agent self-manages short-lived sessions per
    # phase (LOAD / audit / SAVE), so no session is held idle across the OCR + structuring run.
    async def work() -> None:
        from app.ai.agents.syllabus_extraction_agent import SyllabusExtractionAgent

        agent = SyllabusExtractionAgent()
        await agent.process(job_id=job_id, exam_id=exam_id, file_id=file_id)

    try:
        run_task(work, job_id=job_id, task=self, manage_session=False)
    except Exception as exc:
        logger.exception("extract_syllabus task failed: %s", exc)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
