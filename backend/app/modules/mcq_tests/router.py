import logging
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_admin, require_student
from app.core.database import get_db
from app.core.exceptions import AppException
from app.modules.jobs.models import JobStatus
from app.modules.jobs.schemas import JobOut
from app.modules.jobs.service import create_job, update_job
from app.modules.mcq_tests import service as svc
from app.modules.mcq_tests.models import MCQTestBlueprint
from app.modules.mcq_tests.schemas import (
    BlueprintCreate, BlueprintOut, BlueprintListOut,
    TestSetOut, TestSetListOut, TestSetPreview, PreviewQuestion,
    StudentTestSetOut, AttemptStartOut, StudentQuestion,
    AttemptSubmit, AttemptResultOut,
    AttemptHistoryItem, StudentAnalyticsOut,
)
from app.modules.users.models import User

logger = logging.getLogger(__name__)
router = APIRouter(tags=["mcq_tests"])


# ── Admin: blueprints ─────────────────────────────────────────────────────────

@router.post("/admin/mcq-tests/blueprints", response_model=JobOut, status_code=201)
async def create_blueprint(
    payload: BlueprintCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    # Difficulty distribution, when given, must not exceed the total questions
    # per set (it is a within-total preference, never an extra requirement).
    per_set_total = sum(e.count for e in payload.topic_distribution)
    if payload.difficulty_distribution and payload.difficulty_distribution.total() > per_set_total:
        raise AppException(
            422, "difficulty_exceeds_total",
            "Difficulty distribution total cannot exceed the total questions per set.",
        )
    from app.modules.exams.service import get_exam_or_404
    await get_exam_or_404(db, payload.exam_id)

    blueprint = MCQTestBlueprint(
        exam_id=payload.exam_id,
        test_name=payload.test_name,
        total_time_minutes=payload.total_time_minutes,
        num_sets=payload.num_sets,
        topic_distribution=[e.model_dump() for e in payload.topic_distribution],
        difficulty_distribution=(
            payload.difficulty_distribution.model_dump() if payload.difficulty_distribution else None
        ),
        custom_instruction=payload.custom_instruction,
        status="generating",
        created_by=current_user.id,
    )
    db.add(blueprint)
    await db.flush()

    job = await create_job(
        db,
        job_type="mcq_test_set_generation",
        created_by=current_user.id,
        input_reference={"blueprint_id": str(blueprint.id)},
    )
    blueprint.job_id = job.id
    await db.commit()

    from app.core.celery_client import get_celery
    task = get_celery().send_task(
        "workers.tasks.mcq_test_tasks.generate_test_sets",
        args=[str(job.id), str(blueprint.id)],
        queue="kvi_ai_mcq",
    )
    await update_job(db, job.id, status=JobStatus.processing, celery_task_id=task.id)

    from sqlalchemy import select as sa_select
    from app.modules.jobs.models import ProcessingJob
    result = await db.execute(sa_select(ProcessingJob).where(ProcessingJob.id == job.id))
    return JobOut.model_validate(result.scalar_one())


@router.get("/admin/mcq-tests/blueprints", response_model=BlueprintListOut)
async def list_blueprints(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    items, total = await svc.list_blueprints(db, page=page, per_page=per_page)
    return BlueprintListOut(
        items=[BlueprintOut.model_validate(b) for b in items],
        total=total, page=page, per_page=per_page,
    )


@router.get("/admin/mcq-tests/blueprints/{blueprint_id}", response_model=BlueprintOut)
async def get_blueprint(
    blueprint_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    bp = await svc.get_blueprint(db, blueprint_id)
    if not bp:
        raise AppException(404, "not_found", "Blueprint not found.")
    return BlueprintOut.model_validate(bp)


@router.post("/admin/mcq-tests/blueprints/{blueprint_id}/regenerate", response_model=JobOut)
async def regenerate_blueprint(
    blueprint_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    bp = await svc.get_blueprint(db, blueprint_id)
    if not bp:
        raise AppException(404, "not_found", "Blueprint not found.")

    job = await create_job(
        db,
        job_type="mcq_test_set_generation",
        created_by=current_user.id,
        input_reference={"blueprint_id": str(bp.id)},
    )
    bp.job_id = job.id
    bp.status = "generating"
    await db.commit()

    from app.core.celery_client import get_celery
    task = get_celery().send_task(
        "workers.tasks.mcq_test_tasks.generate_test_sets",
        args=[str(job.id), str(bp.id)],
        queue="kvi_ai_mcq",
    )
    await update_job(db, job.id, status=JobStatus.processing, celery_task_id=task.id)

    from sqlalchemy import select as sa_select
    from app.modules.jobs.models import ProcessingJob
    result = await db.execute(sa_select(ProcessingJob).where(ProcessingJob.id == job.id))
    return JobOut.model_validate(result.scalar_one())


# ── Admin: sets ───────────────────────────────────────────────────────────────

@router.get("/admin/mcq-tests/sets", response_model=TestSetListOut)
async def list_sets(
    blueprint_id: uuid.UUID | None = Query(None),
    status: str = Query(""),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    items, total = await svc.list_sets(
        db, blueprint_id=blueprint_id, status=status or None, page=page, per_page=per_page,
    )
    return TestSetListOut(
        items=[TestSetOut.model_validate(s) for s in items],
        total=total, page=page, per_page=per_page,
    )


@router.get("/admin/mcq-tests/sets/{set_id}", response_model=TestSetPreview)
async def preview_set(
    set_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    s = await svc.get_set(db, set_id)
    if not s:
        raise AppException(404, "not_found", "Test set not found.")
    bp = await svc.get_blueprint(db, s.blueprint_id)
    pairs = await svc.get_set_questions(db, set_id)
    questions = [
        PreviewQuestion(
            id=q.id, question_text=q.question_text, options=q.options,
            correct_option_ids=q.correct_option_ids, explanation=q.explanation,
            topic=q.topic, subtopic=q.subtopic, complexity=q.complexity,
            question_order=order,
        )
        for q, order in pairs
    ]
    return TestSetPreview(
        **TestSetOut.model_validate(s).model_dump(),
        test_name=bp.test_name if bp else s.set_name,
        total_time_minutes=bp.total_time_minutes if bp else 0,
        questions=questions,
    )


@router.post("/admin/mcq-tests/sets/{set_id}/activate", response_model=TestSetOut)
async def activate_set(
    set_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    s = await svc.get_set(db, set_id)
    if not s:
        raise AppException(404, "not_found", "Test set not found.")
    if s.num_questions == 0:
        raise AppException(422, "empty_set", "Cannot activate a set with no questions.")
    s = await svc.set_status(db, set_id, "active")
    return TestSetOut.model_validate(s)


@router.post("/admin/mcq-tests/sets/{set_id}/deactivate", response_model=TestSetOut)
async def deactivate_set(
    set_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    s = await svc.set_status(db, set_id, "draft")
    if not s:
        raise AppException(404, "not_found", "Test set not found.")
    return TestSetOut.model_validate(s)


@router.post("/admin/mcq-tests/sets/{set_id}/archive", response_model=TestSetOut)
async def archive_set(
    set_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    s = await svc.set_status(db, set_id, "archived")
    if not s:
        raise AppException(404, "not_found", "Test set not found.")
    return TestSetOut.model_validate(s)


@router.delete("/admin/mcq-tests/sets/{set_id}", status_code=204)
async def delete_set(
    set_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    ok = await svc.delete_set(db, set_id)
    if not ok:
        raise AppException(404, "not_found", "Test set not found.")


# ── Student: take a test ──────────────────────────────────────────────────────

@router.get("/student/mcq-tests", response_model=list[StudentTestSetOut])
async def list_student_tests(
    exam_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_student),
):
    rows = await svc.list_student_tests(db, current_user.id, exam_id=exam_id)
    return [StudentTestSetOut(**r) for r in rows]


@router.post("/student/mcq-tests/{set_id}/start", response_model=AttemptStartOut)
async def start_test(
    set_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_student),
):
    s = await svc.get_set(db, set_id)
    if not s:
        raise AppException(404, "not_found", "Test is not available.")

    existing = await svc.get_student_attempt(db, set_id, current_user.id)
    # No retakes — a submitted attempt can only be viewed via the result endpoint.
    if existing and existing.status == "submitted":
        raise AppException(409, "already_attempted", "You have already attempted this test. No retakes are allowed.")
    # Allow starting only an active set; but always allow resuming (Continue) an
    # already-started attempt even if the admin later deactivated the set.
    if not existing and s.status != "active":
        raise AppException(404, "not_found", "Test is not available.")
    if not existing:
        # Enrollment is an access boundary, not just a listing filter — a NEW attempt
        # requires enrollment in the set's exam (an existing attempt stays resumable,
        # matching the deactivated-set resume rule above).
        from app.modules.exams.service import ensure_enrolled
        await ensure_enrolled(db, student_id=current_user.id, exam_id=s.exam_id)

    # Race-safe: concurrent/duplicate start requests resume the same attempt
    # instead of violating the unique constraint and 500ing.
    attempt = await svc.get_or_create_attempt(db, s, current_user.id)

    bp = await svc.get_blueprint(db, s.blueprint_id)
    pairs = await svc.get_set_questions(db, set_id)
    questions = [
        StudentQuestion(
            id=q.id, question_text=q.question_text, options=q.options,
            topic=q.topic, complexity=q.complexity, question_order=order,
        )
        for q, order in pairs
    ]
    return AttemptStartOut(
        attempt_id=attempt.id,
        set_id=s.id,
        test_name=bp.test_name if bp else s.set_name,
        total_time_minutes=bp.total_time_minutes if bp else 0,
        started_at=attempt.started_at,
        questions=questions,
    )


@router.post("/student/mcq-tests/{set_id}/submit", response_model=AttemptResultOut)
async def submit_test(
    set_id: uuid.UUID,
    payload: AttemptSubmit,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_student),
):
    attempt = await svc.get_student_attempt(db, set_id, current_user.id)
    if not attempt:
        raise AppException(404, "no_attempt", "Start the test before submitting.")
    # Idempotent: a second submit just returns the already-graded result rather
    # than re-grading (no retake / no score change on a double click).
    if attempt.status != "submitted":
        await svc.submit_attempt(db, attempt, payload.answers, payload.time_taken_seconds)
    result = await svc.build_result(db, attempt)
    return AttemptResultOut(**result)


@router.get("/student/mcq-tests/attempts/{attempt_id}/result", response_model=AttemptResultOut)
async def get_attempt_result(
    attempt_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_student),
):
    attempt = await svc.get_attempt(db, attempt_id)
    if not attempt or attempt.student_id != current_user.id:
        raise AppException(404, "not_found", "Attempt not found.")
    if attempt.status != "submitted":
        raise AppException(409, "not_submitted", "This attempt has not been submitted yet.")
    result = await svc.build_result(db, attempt)
    return AttemptResultOut(**result)


@router.get("/student/mcq-tests/history", response_model=list[AttemptHistoryItem])
async def my_history(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_student),
):
    """This student's own submitted attempts (newest first)."""
    rows = await svc.attempt_history(db, current_user.id)
    return [AttemptHistoryItem(**r) for r in rows]


@router.get("/student/mcq-tests/analytics", response_model=StudentAnalyticsOut)
async def my_analytics(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_student),
):
    """This student's own MCQ analytics (accuracy, averages, weak topics)."""
    data = await svc.student_analytics(db, current_user.id)
    return StudentAnalyticsOut(**data)
