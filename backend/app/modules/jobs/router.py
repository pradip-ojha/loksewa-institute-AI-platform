import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, require_admin
from app.core.database import get_db
from app.core.exceptions import AppException
from app.modules.jobs.models import ProcessingJob
from app.modules.jobs.schemas import JobOut
from app.modules.jobs.service import (
    create_job,
    delete_job as delete_job_service,
    reconcile_orphaned_entities,
)
from app.modules.users.models import User, UserRole

router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(ProcessingJob).where(ProcessingJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise AppException(404, "not_found", "Job not found.")
    # Ownership (was an IDOR: any user could poll any job id and read its error_message
    # + output_reference). Admins see any job; a student only the jobs they created.
    if current_user.role != UserRole.institute_admin and job.created_by != current_user.id:
        raise AppException(404, "not_found", "Job not found.")
    return JobOut.model_validate(job)


@router.delete("/admin/jobs/{job_id}", status_code=200)
async def delete_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Admin force-delete of a job the admin considers stuck.

    Best-effort revokes the Celery task, deletes the job-log row (FKs are ON DELETE
    SET NULL, so no real content is removed), then reconciles any now-orphaned
    dependent entity (knowledge/MCQ document, answer sheet, subjective test, video)
    to `failed` so its UI leaves the spinner and offers retry/re-upload."""
    result = await db.execute(select(ProcessingJob).where(ProcessingJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise AppException(404, "not_found", "Job not found.")

    await delete_job_service(db, job)
    try:
        reconciled = await reconcile_orphaned_entities(db)
    except Exception:
        # Reconcile is best-effort; the periodic reaper will catch anything missed.
        reconciled = 0
    return {"deleted": True, "reconciled": reconciled}


@router.post("/admin/jobs/test", response_model=JobOut, status_code=202)
async def dispatch_test_job(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    job = await create_job(db, job_type="test_simulation", created_by=current_user.id)

    from app.core.celery_client import get_celery
    from app.modules.jobs.service import update_job
    from app.modules.jobs.models import JobStatus
    task = get_celery().send_task("workers.tasks.test_task.simulate_job", args=[str(job.id)])
    await update_job(db, job.id, celery_task_id=task.id)
    await db.refresh(job)
    return JobOut.model_validate(job)


@router.get("/admin/jobs/knowledge", response_model=list[JobOut])
async def list_knowledge_jobs(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(
        select(ProcessingJob)
        .where(ProcessingJob.job_type == "knowledge_processing")
        .order_by(ProcessingJob.created_at.desc())
        .limit(limit)
    )
    return [JobOut.model_validate(j) for j in result.scalars().all()]
