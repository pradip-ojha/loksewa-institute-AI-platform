import logging
import uuid

from workers.celery_app import celery_app
from workers.runtime import run_task

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="workers.tasks.mcq_test_tasks.generate_test_sets",
    max_retries=2,
    default_retry_delay=30,
)
def generate_test_sets(self, job_id: str, blueprint_id: str) -> None:
    async def work(db) -> None:
        from sqlalchemy import select
        from app.modules.mcq_tests.models import MCQTestBlueprint
        from app.modules.mcq_tests import service as svc
        from app.modules.jobs.service import update_job
        from app.modules.jobs.models import JobStatus

        bp_r = await db.execute(
            select(MCQTestBlueprint).where(MCQTestBlueprint.id == uuid.UUID(blueprint_id))
        )
        blueprint = bp_r.scalar_one_or_none()
        if not blueprint:
            raise ValueError(f"MCQTestBlueprint {blueprint_id} not found")

        await update_job(
            db, uuid.UUID(job_id), status=JobStatus.processing,
            progress=20, step="Selecting questions from the approved pool…",
        )

        result = await svc.generate_sets(db, blueprint)

        # The generator commits its own work; surface the outcome on the job so
        # the admin sees either the sets created or the shortage breakdown.
        await update_job(
            db, uuid.UUID(job_id),
            status=JobStatus.completed, progress=100,
            step=("Generated test sets" if result.get("generated") else "Insufficient questions — shortage reported"),
            output=result,
        )

    try:
        run_task(work, job_id=job_id, task=self)
    except Exception as exc:
        logger.exception("generate_test_sets task failed: %s", exc)
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))
