"""Admin dashboard aggregation.

Single read-only snapshot for the admin home: the headline counts plus a recent
activity feed. Counts are guarded so a partially-migrated database (a table that
does not exist yet) degrades to 0 instead of 500-ing the whole dashboard.
"""
import logging
from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.models import User, UserRole, UserStatus
from app.modules.jobs.models import ProcessingJob, JobStatus

logger = logging.getLogger(__name__)


# Map a processing-job type to the activity feed's (type, friendly title) pair.
# The frontend renders a chip per `type`; unknown types fall back to the raw value.
_JOB_ACTIVITY = {
    "knowledge_processing": ("knowledge_upload", "Knowledge document processed"),
    "mcq_extraction": ("mcq_upload", "MCQs extracted from upload"),
    "mcq_generation": ("mcq_generation", "MCQs generated from content"),
    "mcq_regeneration": ("mcq_generation", "Rejected MCQs regenerated"),
    "mcq_test_set_generation": ("mcq_generation", "MCQ test set generated"),
    "subjective_test_processing": ("answer_submission", "Subjective test skills generated"),
    "answer_sheet_checking": ("answer_submission", "Answer sheet checked"),
    "video_processing": ("video_question", "Video lecture processed"),
    "skill_builder_update": ("skill_update", "Skill update applied"),
}


async def _safe_scalar(db: AsyncSession, stmt) -> int:
    try:
        r = await db.execute(stmt)
        return int(r.scalar_one() or 0)
    except Exception:  # table may not exist on a partial migration
        logger.debug("dashboard count skipped", exc_info=True)
        return 0


async def get_stats(db: AsyncSession) -> dict:
    from app.modules.knowledge.models import KnowledgeDocument
    from app.modules.mcq.models import MCQQuestion
    from app.modules.mcq_tests.models import MCQTestSet
    from app.modules.subjective.models import SubjectiveTest
    from app.modules.video.models import Video

    total_students = await _safe_scalar(
        db, select(func.count()).select_from(User).where(User.role == UserRole.student)
    )
    active_students = await _safe_scalar(
        db,
        select(func.count()).select_from(User).where(
            User.role == UserRole.student, User.status == UserStatus.active
        ),
    )
    pending_jobs = await _safe_scalar(
        db,
        select(func.count()).select_from(ProcessingJob).where(
            ProcessingJob.status.in_([JobStatus.queued, JobStatus.processing, JobStatus.retrying])
        ),
    )
    failed_jobs = await _safe_scalar(
        db,
        select(func.count()).select_from(ProcessingJob).where(
            ProcessingJob.status == JobStatus.failed
        ),
    )
    knowledge = await _safe_scalar(db, select(func.count()).select_from(KnowledgeDocument))
    approved_mcqs = await _safe_scalar(
        db, select(func.count()).select_from(MCQQuestion).where(MCQQuestion.status == "approved")
    )
    active_sets = await _safe_scalar(
        db, select(func.count()).select_from(MCQTestSet).where(MCQTestSet.status == "active")
    )
    subjective = await _safe_scalar(db, select(func.count()).select_from(SubjectiveTest))
    videos = await _safe_scalar(db, select(func.count()).select_from(Video))

    return {
        "total_students": total_students,
        "total_active_students": active_students,
        "total_knowledge_documents": knowledge,
        "total_approved_mcqs": approved_mcqs,
        "total_active_mcq_sets": active_sets,
        "total_subjective_tests": subjective,
        "total_videos": videos,
        "pending_jobs": pending_jobs,
        "failed_jobs": failed_jobs,
        "recent_activity": await _recent_activity(db),
    }


async def _recent_activity(db: AsyncSession, limit: int = 12) -> list[dict]:
    """Most recent processing jobs as a friendly activity feed."""
    try:
        r = await db.execute(
            select(ProcessingJob.job_type, ProcessingJob.status, ProcessingJob.created_at)
            .order_by(ProcessingJob.created_at.desc())
            .limit(limit)
        )
    except Exception:
        return []
    out: list[dict] = []
    for job_type, status, created_at in r.all():
        kind, title = _JOB_ACTIVITY.get(job_type, (job_type, job_type.replace("_", " ").title()))
        out.append({
            "type": kind,
            "title": title,
            "status": status.value if hasattr(status, "value") else str(status),
            "created_at": created_at,
        })
    return out
