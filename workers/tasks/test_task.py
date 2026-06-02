import time

from workers.celery_app import celery_app
from app.modules.jobs.service import update_job_sync


@celery_app.task(bind=True, name="workers.tasks.test_task.simulate_job")
def simulate_job(self, job_id: str):
    steps = [
        (10, "Initializing…"),
        (30, "Loading data…"),
        (55, "Processing…"),
        (75, "Running analysis…"),
        (90, "Finalizing…"),
        (100, "Complete"),
    ]
    try:
        for progress, step in steps:
            update_job_sync(job_id, status="processing", progress=progress, step=step)
            time.sleep(1.5)
        update_job_sync(job_id, status="completed", progress=100, step="Completed")
    except Exception as exc:
        update_job_sync(job_id, status="failed", error=str(exc))
        raise
