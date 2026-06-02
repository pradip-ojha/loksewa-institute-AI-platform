import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.modules.jobs.models import JobStatus, ProcessingJob


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


def update_job_sync(
    job_id: str,
    *,
    status: str | None = None,
    progress: int | None = None,
    step: str | None = None,
    error: str | None = None,
    output: dict | None = None,
) -> None:
    """Sync wrapper for use inside Celery tasks (asyncio.run)."""
    async def _run():
        async with AsyncSessionLocal() as db:
            await update_job(
                db,
                uuid.UUID(job_id),
                status=JobStatus(status) if status else None,
                progress=progress,
                step=step,
                error=error,
                output=output,
            )
    asyncio.run(_run())
