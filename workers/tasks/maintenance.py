"""Periodic maintenance tasks (run by Celery beat)."""
import logging

from workers.celery_app import celery_app
from workers.runtime import run_async

logger = logging.getLogger(__name__)


@celery_app.task(
    name="workers.tasks.maintenance.reap_stale_jobs",
    queue="kvi_ai_default",
    ignore_result=True,
)
def reap_stale_jobs() -> None:
    """Fail any job stuck in queued/processing past its deadline.

    This is the cross-platform backstop that guarantees no job spins forever in
    the UI, even when a worker died mid-task or a terminal write was lost. It is
    NOT tied to a single job row, so it runs the work directly on the persistent
    loop rather than through `run_task`.
    """
    async def work() -> None:
        from app.core.config import settings
        from app.core.database import AsyncSessionLocal
        from app.modules.jobs.service import reap_stale_jobs as _reap

        async with AsyncSessionLocal() as db:
            reaped = await _reap(db, task_timeout_seconds=settings.TASK_TIMEOUT_SECONDS)
            if reaped:
                logger.warning("Reaped %d stale job(s) → failed", reaped)

    run_async(work())
