"""Periodic maintenance tasks (run by Celery beat)."""
import logging

from workers.celery_app import celery_app
from workers.runtime import get_loop, run_async

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
        from app.modules.subjective.service import fail_orphaned_sheets_and_tests

        async with AsyncSessionLocal() as db:
            reaped = await _reap(db, task_timeout_seconds=settings.TASK_TIMEOUT_SECONDS)
            if reaped:
                logger.warning("Reaped %d stale job(s) → failed", reaped)
            # Propagate failed/dead jobs to their answer sheets / tests so the UI
            # leaves the spinner and the student can re-upload.
            reconciled = await fail_orphaned_sheets_and_tests(db)
            if reconciled:
                logger.warning("Reconciled %d orphaned subjective sheet(s)/test(s) → failed", reconciled)

    # The persistent loop runs ONE task at a time. If a long task (e.g. video
    # processing) is currently occupying it, calling run_until_complete here would
    # raise "event loop is already running". Skip this tick — it's a 2-min backstop,
    # the in-task wait_for timeout already bounds a genuinely wedged task, and the
    # next tick reaps once the loop is free.
    if get_loop().is_running():
        logger.info("Worker loop busy with a task; skipping this reap tick.")
        return
    run_async(work())
