import asyncio
import uuid

from workers.celery_app import celery_app
from workers.runtime import run_task


@celery_app.task(bind=True, name="workers.tasks.test_task.simulate_job")
def simulate_job(self, job_id: str):
    async def work(db) -> None:
        from app.modules.jobs.models import JobStatus
        from app.modules.jobs.service import update_job

        steps = [
            (10, "Initializing…"),
            (30, "Loading data…"),
            (55, "Processing…"),
            (75, "Running analysis…"),
            (90, "Finalizing…"),
            (100, "Complete"),
        ]
        job_uuid = uuid.UUID(job_id)
        for progress, step in steps:
            await update_job(db, job_uuid, status=JobStatus.processing, progress=progress, step=step)
            await asyncio.sleep(1.5)
        await update_job(db, job_uuid, status=JobStatus.completed, progress=100, step="Completed")

    run_task(work, job_id=job_id, task=self)
