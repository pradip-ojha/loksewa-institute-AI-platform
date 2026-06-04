import logging

from workers.celery_app import celery_app
from workers.runtime import run_task

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="workers.tasks.skill_tasks.update_mcq_skill_from_rejection",
    max_retries=1,
    default_retry_delay=30,
)
def update_mcq_skill_from_rejection(self, job_id: str, feedback: str, samples: list[dict]) -> None:
    """Refine the MCQGenerationAgent skill from admin rejection feedback.

    Runs on the kvi_ai_skill queue as a tracked job (replaces the old untracked
    FastAPI BackgroundTask), so a failed refinement surfaces on the job instead
    of disappearing into a log warning.
    """
    async def work(db) -> None:
        from app.modules.skill_layer.service import update_skill_from_rejection

        await update_skill_from_rejection(db, feedback, samples)

    try:
        run_task(work, job_id=job_id, task=self)
    except Exception as exc:
        logger.exception("update_mcq_skill_from_rejection task failed: %s", exc)
        raise self.retry(exc=exc, countdown=30)
