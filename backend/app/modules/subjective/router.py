import logging
import uuid

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_admin, require_student
from app.core.database import get_db
from app.core.exceptions import AppException
from app.core.ratelimit import AI_CHAT_LIMIT, limiter
from app.modules.files.service import store_upload
from app.modules.jobs.models import JobStatus, ProcessingJob
from app.modules.jobs.schemas import JobOut
from app.modules.jobs.service import create_job, update_job
from app.modules.subjective import service as svc
from app.modules.subjective.models import StudentAnswerSheet, SubjectiveTest
from app.modules.subjective.schemas import (
    AnswerResultOut, FeedbackChatMessageIn, FeedbackChatOut, FeedbackChatReplyOut,
    StudentTestDetailOut, StudentTestListItem, SubjectiveQuestionOut,
    SubjectiveTestDetailOut, SubjectiveTestOut, SubmissionOut, TestListOut,
)
from app.modules.users.models import User

logger = logging.getLogger(__name__)
router = APIRouter(tags=["subjective"])


async def _job_out(db: AsyncSession, job_id: uuid.UUID) -> JobOut:
    r = await db.execute(select(ProcessingJob).where(ProcessingJob.id == job_id))
    return JobOut.model_validate(r.scalar_one())


# ── Admin: create / manage tests ────────────────────────────────────────────────

@router.post("/admin/subjective/tests", response_model=JobOut, status_code=201)
async def create_test(
    display_name: str = Form(...),
    exam_id: uuid.UUID = Form(...),
    total_time_minutes: int = Form(60),
    total_marks: int = Form(0),
    custom_instruction: str | None = Form(None),
    model_answer_is_handwritten: bool = Form(False),
    question_paper: UploadFile = File(...),
    model_answer: UploadFile | None = File(None),
    sample_marked: UploadFile | None = File(None),
    rubric: UploadFile | None = File(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Create + configure a subjective test. Stores the supplied files, then runs
    a background job that extracts the questions/marks from the paper and
    generates the internal per-question checking guides."""
    from app.modules.exams.service import get_exam_or_404
    await get_exam_or_404(db, exam_id)
    paper = await store_upload(
        question_paper, context="subjective-tests",
        display_name=f"{display_name} — Question Paper",
        uploaded_by=current_user.id, db=db,
    )
    model_file = (
        await store_upload(model_answer, context="subjective-tests",
                           display_name=f"{display_name} — Model Answer",
                           uploaded_by=current_user.id, db=db)
        if model_answer else None
    )
    sample_file = (
        await store_upload(sample_marked, context="subjective-tests",
                           display_name=f"{display_name} — Sample Marked",
                           uploaded_by=current_user.id, db=db)
        if sample_marked else None
    )
    rubric_file = (
        await store_upload(rubric, context="subjective-tests",
                           display_name=f"{display_name} — Rubric",
                           uploaded_by=current_user.id, db=db)
        if rubric else None
    )

    test = SubjectiveTest(
        display_name=display_name,
        exam_id=exam_id,
        total_time_minutes=total_time_minutes,
        total_marks=total_marks,
        custom_instruction=custom_instruction,
        question_paper_file_id=paper.id,
        model_answer_file_id=model_file.id if model_file else None,
        model_answer_is_handwritten=model_answer_is_handwritten,
        sample_marked_file_id=sample_file.id if sample_file else None,
        rubric_file_id=rubric_file.id if rubric_file else None,
        status="draft",
        skill_generation_status="processing",
        created_by=current_user.id,
    )
    db.add(test)
    await db.flush()

    job = await create_job(
        db, job_type="subjective_test_processing",
        created_by=current_user.id,
        input_reference={"test_id": str(test.id)},
    )
    test.skill_generation_job_id = job.id
    await db.commit()

    from app.core.celery_client import get_celery
    task = get_celery().send_task(
        "workers.tasks.subjective_tasks.generate_test_skills",
        args=[str(job.id), str(test.id)],
        queue="kvi_ai_subjective",
    )
    await update_job(db, job.id, status=JobStatus.processing, celery_task_id=task.id)
    return await _job_out(db, job.id)


@router.get("/admin/subjective/tests", response_model=TestListOut)
async def list_tests(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    items, total = await svc.list_tests(db, page=page, per_page=per_page)
    return TestListOut(
        items=[SubjectiveTestOut(**svc.test_out_fields(t)) for t in items],
        total=total, page=page, per_page=per_page,
    )


@router.get("/admin/subjective/tests/{test_id}", response_model=SubjectiveTestDetailOut)
async def get_test(
    test_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    t = await svc.get_test(db, test_id)
    if not t:
        raise AppException(404, "not_found", "Test not found.")
    questions = await svc.get_test_questions(db, test_id)
    return SubjectiveTestDetailOut(
        **svc.test_out_fields(t),
        custom_instruction=t.custom_instruction,
        question_paper_url=await svc.signed_url(db, t.question_paper_file_id),
        model_answer_url=await svc.signed_url(db, t.model_answer_file_id),
        rubric_url=await svc.signed_url(db, t.rubric_file_id),
        questions=[SubjectiveQuestionOut.model_validate(q) for q in questions],
    )


@router.post("/admin/subjective/tests/{test_id}/activate", response_model=SubjectiveTestOut)
async def activate_test(
    test_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    t = await svc.get_test(db, test_id)
    if not t:
        raise AppException(404, "not_found", "Test not found.")
    if t.skill_generation_status != "completed":
        raise AppException(422, "not_ready", "Question extraction / skill generation has not completed yet.")
    if t.num_questions == 0:
        raise AppException(422, "empty_test", "Cannot activate a test with no questions.")
    t = await svc.set_status(db, test_id, "active")
    return SubjectiveTestOut(**svc.test_out_fields(t))


@router.post("/admin/subjective/tests/{test_id}/regenerate-skills", response_model=JobOut, status_code=201)
async def regenerate_skills(
    test_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Re-run the multi-agent skill generation for an existing test (e.g. to upgrade
    skills created by an older pipeline). Replaces the test's questions and locked
    checking skills."""
    t = await svc.get_test(db, test_id)
    if not t:
        raise AppException(404, "not_found", "Test not found.")

    t.skill_generation_status = "processing"
    job = await create_job(
        db, job_type="subjective_test_processing",
        created_by=current_user.id,
        input_reference={"test_id": str(test_id), "regenerate": True},
    )
    t.skill_generation_job_id = job.id
    await db.commit()

    from app.core.celery_client import get_celery
    task = get_celery().send_task(
        "workers.tasks.subjective_tasks.generate_test_skills",
        args=[str(job.id), str(test_id)],
        queue="kvi_ai_subjective",
    )
    await update_job(db, job.id, status=JobStatus.processing, celery_task_id=task.id)
    return await _job_out(db, job.id)


@router.post("/admin/subjective/tests/{test_id}/archive", response_model=SubjectiveTestOut)
async def archive_test(
    test_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    t = await svc.set_status(db, test_id, "archived")
    if not t:
        raise AppException(404, "not_found", "Test not found.")
    return SubjectiveTestOut(**svc.test_out_fields(t))


@router.delete("/admin/subjective/tests/{test_id}", status_code=204)
async def delete_test(
    test_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    ok = await svc.delete_test(db, test_id)
    if not ok:
        raise AppException(404, "not_found", "Test not found.")


@router.get("/admin/subjective/tests/{test_id}/submissions", response_model=list[SubmissionOut])
async def list_submissions(
    test_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    rows = await svc.list_submissions(db, test_id)
    return [SubmissionOut(**r) for r in rows]


# ── Admin debug: per-step pipeline output (for low-level optimization) ────────────

@router.get("/admin/subjective/tests/{test_id}/skill-debug")
async def skill_generation_debug(
    test_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Every step's output of the question-paper → checking-skill generation workflow:
    extracted questions + marks, detected topic/subtopic, the locked per-question
    checking guide with its evaluator verdict + iteration count + the knowledge chunks
    fetched from Pinecone for that question, and every AI call. Admin-only — exposes
    internal JSON for tuning the skill-generation pipeline."""
    debug = await svc.build_skill_debug(db, test_id)
    if debug is None:
        raise AppException(404, "not_found", "Test not found.")
    return debug


@router.get("/admin/subjective/sheets/{sheet_id}/debug")
async def answer_sheet_debug(
    sheet_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Every step's output of the answer-sheet checking pipeline for one sheet:
    quality gate → question-level extraction → locked skills used → checker (initial)
    → reviewer (final) → annotation locator + geometry validation → draw commands →
    checked PDF, plus every AI call. Admin-only — exposes internal JSON for tuning."""
    debug = await svc.build_sheet_debug(db, sheet_id)
    if debug is None:
        raise AppException(404, "not_found", "Answer sheet not found.")
    return debug


@router.post("/admin/subjective/sheets/{sheet_id}/reannotate", response_model=JobOut, status_code=201)
async def reannotate_sheet(
    sheet_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Re-run ONLY the annotation phase (vision locator → geometry validator → renderer)
    on the sheet's stored extraction + final evaluation, producing a fresh checked PDF.
    Marks/feedback are never touched — this is the cheap iteration loop for tuning
    annotation placement (no re-extraction, no checker/reviewer passes)."""
    sheet = await svc.get_sheet(db, sheet_id)
    if not sheet:
        raise AppException(404, "not_found", "Answer sheet not found.")
    if sheet.current_status not in ("feedback_ready", "checked"):
        raise AppException(409, "not_checked", "The sheet has no completed evaluation to re-annotate.")

    job = await create_job(
        db, job_type="pdf_annotation",
        created_by=current_user.id,
        input_reference={"sheet_id": str(sheet_id), "reannotate": True},
    )
    await db.commit()

    from app.core.celery_client import get_celery
    task = get_celery().send_task(
        "workers.tasks.subjective_tasks.reannotate_sheet",
        args=[str(job.id), str(sheet_id)],
        queue="kvi_ai_subjective",
    )
    await update_job(db, job.id, status=JobStatus.processing, celery_task_id=task.id)
    return await _job_out(db, job.id)


@router.get("/admin/subjective/sheets/{sheet_id}/debug-pdf")
async def answer_sheet_debug_pdf(
    sheet_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Coordinate debug: re-renders the sheet and overlays the locator/validation
    geometry (raw vs. final + page corners) into a diagnostic PDF. Use to confirm an
    annotation mismatch is geometry vs. rendering style. Admin-only."""
    debug = await svc.build_debug_pdf(db, sheet_id)
    if debug is None:
        raise AppException(404, "not_found", "Answer sheet not found.")
    return debug


# ── Student ─────────────────────────────────────────────────────────────────────

@router.get("/student/subjective/tests", response_model=list[StudentTestListItem])
async def student_list_tests(
    exam_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_student),
):
    rows = await svc.list_student_tests(db, current_user.id, exam_id=exam_id)
    return [StudentTestListItem(**r) for r in rows]


@router.get("/student/subjective/tests/{test_id}", response_model=StudentTestDetailOut)
async def student_get_test(
    test_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_student),
):
    t = await svc.get_test(db, test_id)
    if not t or t.status != "active":
        # Still allow viewing if the student already has a sheet for it.
        existing = await svc.get_latest_sheet(db, test_id, current_user.id) if t else None
        if not t or not existing:
            raise AppException(404, "not_found", "Test is not available.")
    sheet = await svc.get_latest_sheet(db, test_id, current_user.id)
    if sheet is None:
        # Enrollment is an access boundary, not just a listing filter — without it any
        # student with the test UUID could read the question-paper signed URL for an
        # exam they aren't enrolled in. A student with an existing sheet keeps access
        # to their own work even if later un-enrolled.
        from app.modules.exams.service import ensure_enrolled
        await ensure_enrolled(db, student_id=current_user.id, exam_id=t.exam_id)
    return StudentTestDetailOut(
        test_id=t.id,
        display_name=t.display_name,
        total_time_minutes=t.total_time_minutes,
        num_questions=t.num_questions,
        total_marks=t.total_marks,
        question_paper_url=await svc.signed_url(db, t.question_paper_file_id),
        submission_status=svc._submission_status(sheet),
        sheet_id=sheet.id if sheet else None,
        upload_attempt_number=sheet.upload_attempt_number if sheet else 0,
    )


@router.post("/student/subjective/tests/{test_id}/upload-answer", response_model=JobOut, status_code=201)
async def upload_answer(
    test_id: uuid.UUID,
    answer_sheet: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_student),
):
    t = await svc.get_test(db, test_id)
    if not t or t.status != "active":
        raise AppException(404, "not_found", "Test is not available.")
    # A new submission consumes the full AI checking pipeline — enrolled students only.
    from app.modules.exams.service import ensure_enrolled
    await ensure_enrolled(db, student_id=current_user.id, exam_id=t.exam_id)

    previous = await svc.get_latest_sheet(db, test_id, current_user.id)
    if previous:
        if previous.current_status == "checked":
            raise AppException(409, "already_checked", "Your answer sheet has already been checked.")
        if previous.current_status not in ("needs_reupload", "failed"):
            raise AppException(409, "in_progress", "Your previous submission is still being processed.")
        if previous.upload_attempt_number >= svc.MAX_UPLOAD_ATTEMPTS:
            # Final attempt exhausted — the checker proceeds with a warning instead
            # of allowing further re-uploads (CLAUDE.md §12).
            raise AppException(409, "max_attempts", "Maximum re-upload attempts reached.")

    attempt_number = (previous.upload_attempt_number + 1) if previous else 1

    file_record = await store_upload(
        answer_sheet, context="answer-sheets",
        display_name=f"{t.display_name} — Answer ({current_user.full_name}, attempt {attempt_number})",
        uploaded_by=current_user.id, db=db,
    )

    sheet = StudentAnswerSheet(
        test_id=test_id,
        student_id=current_user.id,
        file_id=file_record.id,
        upload_attempt_number=attempt_number,
        current_status="uploaded",
    )
    db.add(sheet)
    try:
        await db.flush()
    except IntegrityError:
        # A concurrent upload for the same (test, student) already claimed this attempt
        # number (uq_answer_sheet_test_student_attempt). Treat as a duplicate submission
        # rather than inserting a second sheet + a second checking job.
        await db.rollback()
        raise AppException(409, "in_progress", "Your submission is already being processed.")

    job = await create_job(
        db, job_type="answer_sheet_checking",
        created_by=current_user.id,
        input_reference={"sheet_id": str(sheet.id), "test_id": str(test_id)},
    )
    sheet.checking_job_id = job.id
    await db.commit()

    from app.core.celery_client import get_celery
    task = get_celery().send_task(
        "workers.tasks.subjective_tasks.check_answer_sheet",
        args=[str(job.id), str(sheet.id)],
        queue="kvi_ai_subjective",
    )
    await update_job(db, job.id, status=JobStatus.processing, celery_task_id=task.id)
    return await _job_out(db, job.id)


@router.get("/student/subjective/tests/{test_id}/result", response_model=AnswerResultOut)
async def student_result(
    test_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_student),
):
    sheet = await svc.get_latest_sheet(db, test_id, current_user.id)
    if not sheet:
        raise AppException(404, "no_submission", "You have not submitted an answer sheet for this test.")
    result = await svc.build_student_result(db, sheet)
    return AnswerResultOut(**result)


# ── Student: answer-sheet feedback chatbot ───────────────────────────────────────

@router.get("/student/subjective/sheets/{sheet_id}/feedback-chat", response_model=FeedbackChatOut)
async def feedback_chat_history(
    sheet_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_student),
):
    return FeedbackChatOut(**await svc.get_feedback_chat(db, sheet_id, current_user.id))


@router.post("/student/subjective/sheets/{sheet_id}/feedback-chat/start", response_model=FeedbackChatOut, status_code=201)
async def feedback_chat_start(
    sheet_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_student),
):
    return FeedbackChatOut(**await svc.start_feedback_chat(db, sheet_id, current_user.id))


@router.post("/student/subjective/sheets/{sheet_id}/feedback-chat/{chat_id}/message", response_model=FeedbackChatReplyOut)
@limiter.limit(AI_CHAT_LIMIT)
async def feedback_chat_message(
    request: Request,
    sheet_id: uuid.UUID,
    chat_id: uuid.UUID,
    body: FeedbackChatMessageIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_student),
):
    question = (body.message or "").strip()
    if not question:
        raise AppException(422, "empty_question", "Question cannot be empty.")
    result = await svc.post_feedback_question(db, sheet_id, chat_id, question, current_user.id)
    return FeedbackChatReplyOut(**result)
