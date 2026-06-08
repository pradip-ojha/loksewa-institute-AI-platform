import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.jobs.models import JobStatus, ProcessingJob

# How long a job may sit queued before we assume the worker never picked it up.
QUEUED_GRACE_SECONDS = 600        # 10 minutes
# Extra grace on top of the task hard-timeout before we declare a processing job dead.
PROCESSING_GRACE_SECONDS = 300    # 5 minutes


async def create_job(
    db: AsyncSession,
    *,
    job_type: str,
    created_by: uuid.UUID,
    input_reference: dict | None = None,
) -> ProcessingJob:
    job = ProcessingJob(
        job_type=job_type,
        status=JobStatus.queued,
        progress_percent=0,
        input_reference=input_reference,
        created_by=created_by,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def update_job(
    db: AsyncSession,
    job_id: uuid.UUID,
    *,
    status: JobStatus | None = None,
    progress: int | None = None,
    step: str | None = None,
    error: str | None = None,
    output: dict | None = None,
    celery_task_id: str | None = None,
) -> None:
    result = await db.execute(select(ProcessingJob).where(ProcessingJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        return
    now = datetime.now(timezone.utc)
    if status:
        job.status = status
        if status == JobStatus.processing and not job.started_at:
            job.started_at = now
        if status in (JobStatus.completed, JobStatus.failed, JobStatus.cancelled):
            job.completed_at = now
    if progress is not None:
        job.progress_percent = progress
    if step is not None:
        job.current_step = step
    if error is not None:
        job.error_message = error
    if output is not None:
        job.output_reference = output
    if celery_task_id is not None:
        job.celery_task_id = celery_task_id
    await db.commit()


async def reap_stale_jobs(db: AsyncSession, *, task_timeout_seconds: int) -> int:
    """Fail jobs that can never finish — the system-level backstop for stuck jobs.

    Two cases, both detected from existing timestamps (no schema change):
      • `queued` longer than QUEUED_GRACE_SECONDS  → worker never picked it up
        (e.g. an orphaned/misrouted message). started_at is still NULL.
      • `processing` longer than the hard task timeout + grace → the worker died
        or wedged without recording a terminal state (covers the Windows case
        where Celery's hard time limit can't fire).

    This guarantees the UI poller always reaches a terminal state even if a
    terminal write was lost. Returns the number of jobs reaped.
    """
    now = datetime.now(timezone.utc)
    queued_cutoff = now - timedelta(seconds=QUEUED_GRACE_SECONDS)
    processing_cutoff = now - timedelta(seconds=task_timeout_seconds + PROCESSING_GRACE_SECONDS)

    stale_queued = (ProcessingJob.status == JobStatus.queued) & (
        ProcessingJob.created_at < queued_cutoff
    )
    stale_processing = (ProcessingJob.status == JobStatus.processing) & (
        ProcessingJob.started_at < processing_cutoff
    )

    result = await db.execute(
        select(ProcessingJob).where(or_(stale_queued, stale_processing))
    )
    stale = result.scalars().all()
    if not stale:
        return 0

    for job in stale:
        job.status = JobStatus.failed
        job.completed_at = now
        if job.started_at is None:
            job.error_message = "Worker did not pick up this task (timed out in queue)."
        else:
            job.error_message = "Exceeded maximum processing time; the worker did not finish."
    await db.commit()
    return len(stale)


async def fail_orphaned_processing_jobs(db: AsyncSession) -> int:
    """Fail every job still marked `processing` — called once when a worker starts.

    A freshly-started worker owns no in-flight tasks, so any `processing` row is
    orphaned: its worker died (e.g. the backend/worker was restarted mid-task). We
    fail them immediately so the UI doesn't spin forever and the user can retry.
    `queued` jobs are left alone — the broker may still legitimately deliver them.
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(ProcessingJob).where(ProcessingJob.status == JobStatus.processing)
    )
    orphaned = result.scalars().all()
    for job in orphaned:
        job.status = JobStatus.failed
        job.completed_at = now
        job.error_message = "Worker restarted while this task was running; marked failed."
    if orphaned:
        await db.commit()
    return len(orphaned)
