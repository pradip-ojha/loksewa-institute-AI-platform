import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.jobs.models import JobStatus, ProcessingJob

# How long a job may sit queued before we assume the worker never picked it up.
QUEUED_GRACE_SECONDS = 600        # 10 minutes
# How often a running worker refreshes its job's heartbeat. The worker runs an
# independent heartbeat loop (see workers/runtime.py), so the heartbeat keeps
# advancing even during a multi-minute AI/render phase with no progress update.
HEARTBEAT_INTERVAL_SECONDS = 30
# A `processing` job whose heartbeat is older than this is considered dead (its
# worker crashed / was killed). 6 missed beats of margin keeps a transient DB blip
# from falsely reaping a live job, while still detecting a real death in ~3 min
# instead of waiting out the full task timeout. Used by BOTH startup recovery and
# the periodic reaper, so "dead vs. alive" has a single definition.
JOB_STALE_SECONDS = HEARTBEAT_INTERVAL_SECONDS * 6   # 180s


def _last_sign_of_life():
    """Best timestamp proving a processing job's worker was alive: the heartbeat if
    present, else when it started, else when it was created."""
    return func.coalesce(
        ProcessingJob.last_heartbeat_at,
        ProcessingJob.started_at,
        ProcessingJob.created_at,
    )


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
    owner_token: str | None = None,
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
    if owner_token is not None:
        job.owner_token = owner_token
    # Any update to a still-running job is a sign of life; progress/step updates
    # double as heartbeats on top of the worker's periodic heartbeat loop.
    if job.status == JobStatus.processing:
        job.last_heartbeat_at = now
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


async def touch_heartbeat(db: AsyncSession, job_id: uuid.UUID, *, owner_token: str | None = None) -> None:
    """Refresh a running job's heartbeat (and owner) without loading the row.

    Called on a fixed cadence by the worker's heartbeat loop while a task runs. The
    `status == processing` guard means a heartbeat can never resurrect a job that has
    already been failed/completed (e.g. by the reaper or a redelivery skip)."""
    values: dict = {"last_heartbeat_at": datetime.now(timezone.utc)}
    if owner_token is not None:
        values["owner_token"] = owner_token
    await db.execute(
        update(ProcessingJob)
        .where(ProcessingJob.id == job_id, ProcessingJob.status == JobStatus.processing)
        .values(**values)
    )
    await db.commit()


async def reap_stale_jobs(db: AsyncSession, *, task_timeout_seconds: int) -> int:
    """Fail jobs that can never finish — the system-level backstop for stuck jobs.

    Two cases:
      • `queued` longer than QUEUED_GRACE_SECONDS  → worker never picked it up
        (e.g. an orphaned/misrouted message). started_at is still NULL.
      • `processing` whose HEARTBEAT has gone stale (JOB_STALE_SECONDS) → the worker
        died or wedged without recording a terminal state. Heartbeat-based detection
        (not "started_at + task timeout") is both multi-worker safe — a live worker
        keeps its job's heartbeat fresh, so this never reaps another worker's running
        job — and far faster (a dead job is caught in ~3 min, not after the full task
        timeout). Covers the Windows case where Celery's hard time limit can't fire.

    This guarantees the UI poller always reaches a terminal state even if a
    terminal write was lost. Returns the number of jobs reaped. `task_timeout_seconds`
    is retained for signature compatibility; staleness is now heartbeat-driven.
    """
    now = datetime.now(timezone.utc)
    queued_cutoff = now - timedelta(seconds=QUEUED_GRACE_SECONDS)
    stale_cutoff = now - timedelta(seconds=JOB_STALE_SECONDS)

    stale_queued = (ProcessingJob.status == JobStatus.queued) & (
        ProcessingJob.created_at < queued_cutoff
    )
    stale_processing = (ProcessingJob.status == JobStatus.processing) & (
        _last_sign_of_life() < stale_cutoff
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


async def delete_job(db: AsyncSession, job: ProcessingJob) -> None:
    """Admin force-delete of a single job the admin considers stuck.

    Best-effort revokes the Celery task first (terminate=True) so a worker that is
    still churning on a genuinely wedged task stops wasting resources — on the Windows
    solo pool a terminate can't interrupt a running task (no signals), but it still
    prevents a not-yet-started queued task from running. Then the job row is deleted.
    Tables that point at a job (`*_job_id`) use ON DELETE SET NULL, so no real content
    is removed — only the job-log row (same guarantee as `scripts.wipe_jobs`). The
    caller reconciles the now-orphaned dependent entities so their UI leaves the
    spinner (see `reconcile_orphaned_entities`)."""
    if job.celery_task_id:
        try:
            from app.core.celery_client import get_celery

            get_celery().control.revoke(job.celery_task_id, terminate=True, signal="SIGTERM")
        except Exception:
            # Revoke is best-effort; the delete + 2h wait_for + reaper still bound the task.
            pass
    await db.delete(job)
    await db.commit()


async def reconcile_orphaned_entities(db: AsyncSession) -> int:
    """Flip any in-progress entity whose job is now gone/dead to `failed` (or, for a
    `feedback_ready` sheet, `checked`) so its UI stops spinning — the same reconcile the
    periodic reaper runs, invoked inline right after a manual `delete_job` so the effect
    is immediate instead of waiting up to ~2 min for the next beat tick. Best-effort:
    a failure here never blocks the delete. Returns the number reconciled."""
    from app.modules.knowledge.service import fail_orphaned_knowledge_documents
    from app.modules.mcq.service import fail_orphaned_mcq_documents
    from app.modules.subjective.service import fail_orphaned_sheets_and_tests
    from app.modules.video.service import fail_orphaned_videos

    return (
        await fail_orphaned_sheets_and_tests(db)
        + await fail_orphaned_videos(db)
        + await fail_orphaned_knowledge_documents(db)
        + await fail_orphaned_mcq_documents(db)
    )


async def has_live_job_for_document(db: AsyncSession, document_id: uuid.UUID) -> bool:
    """True if a still-live job (queued/processing/retrying) references this document.

    Knowledge and MCQ documents have no direct job FK — their jobs link back via
    `input_reference->>'document_id'`. Used by the orphan reconcilers to decide whether
    a document stuck in an in-progress `processing_status` still has a job that may yet
    finish it, or is truly orphaned and should be failed."""
    live = (JobStatus.queued, JobStatus.processing, JobStatus.retrying)
    r = await db.execute(
        select(ProcessingJob.id)
        .where(
            ProcessingJob.status.in_(live),
            ProcessingJob.input_reference["document_id"].astext == str(document_id),
        )
        .limit(1)
    )
    return r.first() is not None


async def fail_orphaned_processing_jobs(db: AsyncSession) -> int:
    """Fail `processing` jobs whose heartbeat has gone stale — called when a worker starts.

    CRITICAL (multi-worker safety): we must NOT fail every `processing` row, because
    under prefork concurrency / multiple worker processes a sibling worker may be
    actively running those jobs. Failing them would kill live work mid-flight and flip
    the dependent sheets/tests/videos to `failed` under the user. Instead we only fail
    jobs whose last sign of life (heartbeat) is older than JOB_STALE_SECONDS — i.e. the
    owning worker is genuinely dead. A job orphaned by a crash that still has a recent
    heartbeat is left for the periodic reaper to catch once it goes stale (within a few
    minutes). `queued` jobs are left alone — the broker may still deliver them.
    """
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(seconds=JOB_STALE_SECONDS)
    result = await db.execute(
        select(ProcessingJob).where(
            ProcessingJob.status == JobStatus.processing,
            _last_sign_of_life() < stale_cutoff,
        )
    )
    orphaned = result.scalars().all()
    for job in orphaned:
        job.status = JobStatus.failed
        job.completed_at = now
        job.error_message = "Worker died while this task was running; marked failed."
    if orphaned:
        await db.commit()
    return len(orphaned)
