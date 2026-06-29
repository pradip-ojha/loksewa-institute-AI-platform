"""Shared async runtime for Celery workers.

Why this exists
---------------
SQLAlchemy's async engine binds pooled connections to the event loop that first
used them. The previous pattern — `asyncio.run()` per task plus a global
`engine.dispose()` in every task's `finally` — was a workaround: it threw the
whole pool away after each task so the next `asyncio.run()` (a brand-new loop)
wouldn't inherit connections bound to a now-closed loop. That is slow, fragile,
and breaks outright under any non-`solo` worker pool or when one `asyncio.run`
is nested inside another.

Instead we keep ONE persistent event loop per worker process and run every task
coroutine on it. Connections stay bound to that single loop for the worker's
whole life, so no per-task disposal is needed — we dispose once at shutdown.

Every task should go through `run_task`, which guarantees the job row reaches a
terminal state (failed/retrying) even when the task body raises, and enforces a
hard timeout so a hung AI/DB call can't pin a worker forever.
"""
import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from celery.signals import worker_ready, worker_shutdown

logger = logging.getLogger(__name__)

_loop: asyncio.AbstractEventLoop | None = None


def get_loop() -> asyncio.AbstractEventLoop:
    """Return this worker process's persistent event loop, creating it once.

    SUPPORTED POOLS ONLY: `solo` (Windows/dev) and `prefork` (Linux/prod). Both
    give each task a single-threaded process, so one shared loop per process is
    safe. Do NOT run under `threads`/`gevent`/`eventlet`: multiple threads
    calling `run_until_complete` on this one loop would corrupt it.
    """
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop


def run_async(coro: Awaitable[Any]) -> Any:
    """Run a coroutine to completion on the worker's persistent loop."""
    return get_loop().run_until_complete(coro)


def _sanitize_error(exc: Exception) -> str:
    """Short, safe message for a job's error_message column.

    Keeps the exception type and message but truncates, so we never dump a huge
    traceback or a connection string into a user-visible field.
    """
    text = f"{type(exc).__name__}: {exc}"
    return text[:480]


async def _record_terminal(job_id: uuid.UUID, *, status, message: str | None) -> None:
    """Record a terminal job state in a FRESH session.

    The task's own session may be poisoned by a failed transaction, so failures
    must be written through a clean session to be reliable.
    """
    from app.core.database import AsyncSessionLocal
    from app.modules.jobs.service import update_job

    try:
        async with AsyncSessionLocal() as db:
            await update_job(db, job_id, status=status, error=message)
    except Exception:
        logger.exception("Could not record terminal state for job %s", job_id)


async def _already_terminal(job_uuid: uuid.UUID) -> bool:
    """True if this job already reached a terminal state (completed/failed/cancelled).

    `task_acks_late=True` means a task can be redelivered if a worker died after
    finishing (or after the job was failed by startup/reaper recovery). Re-running
    non-idempotent work would duplicate data or resurrect a job the user already
    re-submitted, so we skip it. NOTE: a Celery *retry* leaves the job in `retrying`
    (not terminal), so retries are unaffected.
    """
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.modules.jobs.models import JobStatus, ProcessingJob

    try:
        async with AsyncSessionLocal() as db:
            row = await db.execute(
                select(ProcessingJob.status).where(ProcessingJob.id == job_uuid)
            )
            status = row.scalar_one_or_none()
            return status in (JobStatus.completed, JobStatus.failed, JobStatus.cancelled)
    except Exception:
        # If we can't check, fall through and let the task run normally.
        logger.exception("Could not check prior status for job %s", job_uuid)
        return False


async def _run_task(work: Callable[..., Awaitable[None]], *, job_id: str, timeout: int, will_retry: bool, manage_session: bool) -> None:
    from app.core.database import AsyncSessionLocal
    from app.modules.jobs.models import JobStatus
    from app.modules.jobs.service import update_job

    job_uuid = uuid.UUID(job_id)
    if await _already_terminal(job_uuid):
        logger.info("Job %s already in a terminal state; skipping redelivered task", job_uuid)
        return
    try:
        # Mark `processing` in its OWN short session that is closed immediately.
        # The session passed into `work` must never be the one we reuse for the
        # terminal update: a long task (OCR/embedding/Pinecone) can run for
        # minutes, during which an idle pooled connection is dropped server-side
        # (Neon). Reusing that stale connection for the final commit is exactly
        # the "connection is closed" failure we are eliminating.
        async with AsyncSessionLocal() as mark_db:
            await update_job(mark_db, job_uuid, status=JobStatus.processing, step="Starting…")

        if manage_session:
            # Legacy mode: `work` receives one session held open for its whole run.
            # Simple, but that connection sits IDLE during long AI/render phases and
            # can be dropped server-side mid-task. Use only for short tasks.
            async with AsyncSessionLocal() as db:
                await asyncio.wait_for(work(db), timeout=timeout)
        else:
            # Borrow-per-use mode: `work` takes NO session and opens its own
            # short-lived sessions for each DB touch, returning the connection to
            # the pool during AI/render phases. No connection is ever held idle
            # across a long phase, so the server can't drop it from under us.
            await asyncio.wait_for(work(), timeout=timeout)

        # Guarantee a terminal success state in a FRESH session, never the one
        # held open across `work`. Agents report progress/steps but several never
        # set `completed`, which left jobs stuck at processing.
        async with AsyncSessionLocal() as done_db:
            await update_job(done_db, job_uuid, status=JobStatus.completed, progress=100)
    except asyncio.TimeoutError:
        status = JobStatus.retrying if will_retry else JobStatus.failed
        await _record_terminal(job_uuid, status=status, message=f"Task timed out after {timeout}s")
        raise
    except Exception as exc:
        status = JobStatus.retrying if will_retry else JobStatus.failed
        await _record_terminal(job_uuid, status=status, message=_sanitize_error(exc))
        raise


def run_task(
    work: Callable[..., Awaitable[None]],
    *,
    job_id: str,
    timeout: int | None = None,
    task: Any | None = None,
    manage_session: bool = True,
) -> None:
    """Run a task coroutine on the persistent loop with job-status + timeout safety.

    `work` should do the actual job and set the job to `completed` (with output)
    on success. If it raises, this helper records the failure (as `retrying` while
    Celery retries remain, otherwise `failed`) and re-raises so the Celery task can
    retry.

    `manage_session` (default True): `work` is called as `work(db)` with one open
    AsyncSession held for its whole run — fine for SHORT tasks. Set False for LONG
    tasks (multi-minute AI/render pipelines): `work` is then called as `work()` with
    NO session and must open its own short-lived sessions per DB touch, so a pooled
    connection is never held idle across a long phase (where the server would drop
    it). See `check_answer_sheet` for the borrow-per-use pattern.

    Pass the bound Celery `self` as `task` so the final-vs-retry distinction is
    accurate.
    """
    from app.core.config import settings

    if timeout is None:
        timeout = settings.TASK_TIMEOUT_SECONDS

    will_retry = False
    if task is not None:
        max_retries = getattr(task, "max_retries", 0) or 0
        will_retry = task.request.retries < max_retries

    run_async(_run_task(work, job_id=job_id, timeout=timeout, will_retry=will_retry, manage_session=manage_session))


@worker_ready.connect
def _recover_orphaned_jobs_on_start(**_kwargs) -> None:
    """When a worker boots, fail any job left `processing` by a previous run (its
    worker died / was restarted mid-task) and reconcile the dependent answer sheets
    / tests to `failed`. This stops the UI spinning forever and lets the student
    re-upload. A fresh worker owns no in-flight tasks, so every `processing` row is
    orphaned. Redelivered orphan tasks are then skipped by `_already_terminal`."""
    async def _recover() -> None:
        from app.core.database import AsyncSessionLocal
        from app.modules.jobs.service import fail_orphaned_processing_jobs
        from app.modules.subjective.service import fail_orphaned_sheets_and_tests

        async with AsyncSessionLocal() as db:
            failed = await fail_orphaned_processing_jobs(db)
            reconciled = await fail_orphaned_sheets_and_tests(db)
        if failed or reconciled:
            logger.warning("Startup recovery: failed %d orphaned job(s), reconciled %d sheet(s)/test(s)",
                           failed, reconciled)

    try:
        run_async(_recover())
    except Exception:
        logger.exception("Startup orphaned-job recovery failed")


@worker_shutdown.connect
def _dispose_engine_on_shutdown(**_kwargs) -> None:
    """Dispose the async engine once, when the worker stops — replaces the old
    per-task dispose hack."""
    try:
        from app.core.database import engine
        run_async(engine.dispose())
    except Exception:
        logger.exception("Engine dispose on worker shutdown failed")
