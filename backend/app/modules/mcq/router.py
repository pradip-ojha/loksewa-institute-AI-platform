import uuid

from fastapi import APIRouter, Depends, Query, UploadFile, File as FastAPIFile, Form
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_admin
from app.core.database import get_db
from app.core.exceptions import AppException
from app.modules.files.service import store_upload
from app.modules.jobs.service import create_job, update_job
from app.modules.jobs.models import JobStatus
from app.modules.jobs.schemas import JobOut
from app.modules.mcq.models import MCQDocument, MCQReviewBatch, MCQQuestion, MCQRejectionFeedback
from app.modules.mcq import service as mcq_service
from app.modules.mcq.schemas import (
    MCQDocumentOut, MCQReviewBatchOut, MCQBatchWithQuestions,
    MCQQuestionOut, MCQQuestionCreate, MCQQuestionUpdate,
    MCQQuestionListOut, RejectQuestionRequest, RejectAllRequest,
)
from app.modules.users.models import User

router = APIRouter(tags=["mcq"])


# ── Document uploads ──────────────────────────────────────────────────────────

class UploadResult(BaseModel):
    document_id: uuid.UUID
    job_id: uuid.UUID


@router.post("/admin/mcq/documents/upload", response_model=UploadResult, status_code=201)
async def upload_mcq_document(
    file: UploadFile = FastAPIFile(...),
    display_name: str = Form(...),
    topic: str = Form(""),
    subtopic: str = Form(""),
    custom_instruction: str = Form(""),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    file_record = await store_upload(
        file,
        context="mcq-documents",
        display_name=display_name,
        uploaded_by=current_user.id,
        db=db,
    )

    doc = MCQDocument(
        display_name=display_name,
        origin_type="uploaded_document",
        file_id=file_record.id,
        topic=topic or None,
        subtopic=subtopic or None,
        custom_instruction=custom_instruction or None,
        processing_status="pending",
        created_by=current_user.id,
    )
    db.add(doc)
    await db.flush()

    job = await create_job(
        db,
        job_type="mcq_extraction",
        created_by=current_user.id,
        input_reference={"document_id": str(doc.id)},
    )
    await db.commit()
    await db.refresh(doc)

    from app.core.celery_client import get_celery
    task = get_celery().send_task(
        "workers.tasks.mcq_tasks.extract_mcqs",
        args=[str(job.id), str(doc.id)],
        queue="kvi_ai_mcq",
    )
    await update_job(db, job.id, status=JobStatus.processing, celery_task_id=task.id)

    return UploadResult(document_id=doc.id, job_id=job.id)


@router.post("/admin/mcq/generate", response_model=UploadResult, status_code=201)
async def generate_mcqs_from_content(
    file: UploadFile = FastAPIFile(...),
    display_name: str = Form(...),
    count: int = Form(10),
    topic: str = Form(""),
    subtopic: str = Form(""),
    custom_instruction: str = Form(""),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    if count < 1 or count > 100:
        raise AppException(422, "invalid_count", "Count must be between 1 and 100.")

    file_record = await store_upload(
        file,
        context="mcq-documents",
        display_name=display_name,
        uploaded_by=current_user.id,
        db=db,
    )

    doc = MCQDocument(
        display_name=display_name,
        origin_type="generation_source",
        file_id=file_record.id,
        topic=topic or None,
        subtopic=subtopic or None,
        custom_instruction=custom_instruction or None,
        processing_status="pending",
        created_by=current_user.id,
    )
    db.add(doc)
    await db.flush()

    job = await create_job(
        db,
        job_type="mcq_generation",
        created_by=current_user.id,
        input_reference={"document_id": str(doc.id), "count": count},
    )
    await db.commit()
    await db.refresh(doc)

    from app.core.celery_client import get_celery
    task = get_celery().send_task(
        "workers.tasks.mcq_tasks.generate_mcqs",
        args=[str(job.id), str(doc.id), count],
        queue="kvi_ai_mcq",
    )
    await update_job(db, job.id, status=JobStatus.processing, celery_task_id=task.id)

    return UploadResult(document_id=doc.id, job_id=job.id)


# ── Documents list ────────────────────────────────────────────────────────────

class DocumentListOut(BaseModel):
    items: list[MCQDocumentOut]
    total: int
    page: int
    per_page: int


@router.get("/admin/mcq/documents", response_model=DocumentListOut)
async def list_mcq_documents(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    docs, total = await mcq_service.list_documents(db, page=page, per_page=per_page)
    return DocumentListOut(
        items=[MCQDocumentOut.model_validate(d) for d in docs],
        total=total, page=page, per_page=per_page,
    )


# ── Review Batches ────────────────────────────────────────────────────────────

class BatchListOut(BaseModel):
    items: list[MCQReviewBatchOut]
    total: int
    page: int
    per_page: int


@router.get("/admin/mcq/review-batches", response_model=BatchListOut)
async def list_review_batches(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    batches, total = await mcq_service.list_batches(db, page=page, per_page=per_page)
    return BatchListOut(
        items=[MCQReviewBatchOut.model_validate(b) for b in batches],
        total=total, page=page, per_page=per_page,
    )


@router.get("/admin/mcq/review-batches/{batch_id}", response_model=MCQBatchWithQuestions)
async def get_review_batch(
    batch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    batch = await mcq_service.get_batch(db, batch_id)
    if not batch:
        raise AppException(404, "not_found", "Review batch not found.")
    questions = await mcq_service.get_batch_questions(db, batch_id)
    return MCQBatchWithQuestions(
        **MCQReviewBatchOut.model_validate(batch).model_dump(),
        questions=[MCQQuestionOut.model_validate(q) for q in questions],
    )


@router.post("/admin/mcq/review-batches/{batch_id}/questions/{question_id}/accept", response_model=MCQQuestionOut)
async def accept_question(
    batch_id: uuid.UUID,
    question_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    q = await mcq_service.accept_question(db, question_id)
    if not q:
        raise AppException(404, "not_found", "Question not found.")
    return MCQQuestionOut.model_validate(q)


@router.post("/admin/mcq/review-batches/{batch_id}/questions/{question_id}/reject", response_model=MCQQuestionOut)
async def reject_question(
    batch_id: uuid.UUID,
    question_id: uuid.UUID,
    payload: RejectQuestionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    if not payload.feedback.strip():
        raise AppException(422, "feedback_required", "Rejection feedback is required.")
    q = await mcq_service.reject_question(db, question_id, payload.feedback.strip())
    if not q:
        raise AppException(404, "not_found", "Question not found.")
    db.add(MCQRejectionFeedback(batch_id=batch_id, feedback_text=payload.feedback, created_by=current_user.id))
    await db.commit()
    return MCQQuestionOut.model_validate(q)


@router.post("/admin/mcq/review-batches/{batch_id}/accept-all")
async def accept_all(
    batch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    count = await mcq_service.bulk_accept_batch(db, batch_id)
    return {"accepted": count}


@router.post("/admin/mcq/review-batches/{batch_id}/reject-all")
async def reject_all(
    batch_id: uuid.UUID,
    payload: RejectAllRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    if not payload.feedback.strip():
        raise AppException(422, "feedback_required", "Rejection feedback is required.")
    count = await mcq_service.bulk_reject_batch(db, batch_id, payload.feedback.strip())
    return {"rejected": count}


@router.post("/admin/mcq/review-batches/{batch_id}/regenerate", response_model=JobOut)
async def regenerate_batch(
    batch_id: uuid.UUID,
    payload: RejectAllRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    batch = await mcq_service.get_batch(db, batch_id)
    if not batch:
        raise AppException(404, "not_found", "Batch not found.")

    # check there are rejected questions
    rejected_r = await db.execute(
        select(func.count()).select_from(MCQQuestion).where(
            MCQQuestion.review_batch_id == batch_id,
            MCQQuestion.status == "rejected",
        )
    )
    if rejected_r.scalar_one() == 0:
        raise AppException(422, "no_rejected", "No rejected questions to regenerate.")

    job = await create_job(
        db,
        job_type="mcq_regeneration",
        created_by=current_user.id,
        input_reference={"batch_id": str(batch_id)},
    )

    from app.core.celery_client import get_celery
    task = get_celery().send_task(
        "workers.tasks.mcq_tasks.regenerate_rejected_mcqs",
        args=[str(job.id), str(batch_id), payload.feedback],
        queue="kvi_ai_mcq",
    )
    await update_job(db, job.id, status=JobStatus.processing, celery_task_id=task.id)

    from app.modules.jobs.schemas import JobOut
    from sqlalchemy import select as sa_select
    from app.modules.jobs.models import ProcessingJob
    result = await db.execute(sa_select(ProcessingJob).where(ProcessingJob.id == job.id))
    return JobOut.model_validate(result.scalar_one())


# ── Question Bank ─────────────────────────────────────────────────────────────

@router.get("/admin/mcq/questions", response_model=MCQQuestionListOut)
async def list_questions(
    status: str = Query("approved"),
    topic: str = Query(""),
    subtopic: str = Query(""),
    complexity: str = Query(""),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    questions, total = await mcq_service.list_questions(
        db,
        status=status or None,
        topic=topic or None,
        subtopic=subtopic or None,
        complexity=complexity or None,
        page=page,
        per_page=per_page,
    )
    return MCQQuestionListOut(
        items=[MCQQuestionOut.model_validate(q) for q in questions],
        total=total, page=page, per_page=per_page,
    )


@router.post("/admin/mcq/questions", response_model=MCQQuestionOut, status_code=201)
async def create_question(
    payload: MCQQuestionCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    if len(payload.options) != 4:
        raise AppException(422, "invalid_options", "Exactly 4 options required.")
    if not payload.correct_option_ids:
        raise AppException(422, "invalid_answer", "At least one correct option is required.")

    q = MCQQuestion(
        origin_type="manual",
        question_text=payload.question_text,
        options=[o.model_dump() for o in payload.options],
        correct_option_ids=payload.correct_option_ids,
        explanation=payload.explanation,
        chapter=payload.chapter,
        topic=payload.topic,
        subtopic=payload.subtopic,
        complexity=payload.complexity,
        status="approved",
    )
    db.add(q)
    await db.commit()
    await db.refresh(q)
    return MCQQuestionOut.model_validate(q)


@router.put("/admin/mcq/questions/{question_id}", response_model=MCQQuestionOut)
async def update_question(
    question_id: uuid.UUID,
    payload: MCQQuestionUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(MCQQuestion).where(MCQQuestion.id == question_id))
    q = result.scalar_one_or_none()
    if not q:
        raise AppException(404, "not_found", "Question not found.")

    if payload.question_text is not None:
        q.question_text = payload.question_text
    if payload.options is not None:
        q.options = [o.model_dump() for o in payload.options]
    if payload.correct_option_ids is not None:
        q.correct_option_ids = payload.correct_option_ids
    if payload.explanation is not None:
        q.explanation = payload.explanation
    if payload.chapter is not None:
        q.chapter = payload.chapter
    if payload.topic is not None:
        q.topic = payload.topic
    if payload.subtopic is not None:
        q.subtopic = payload.subtopic
    if payload.complexity is not None:
        q.complexity = payload.complexity

    from datetime import datetime
    q.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(q)
    return MCQQuestionOut.model_validate(q)


@router.delete("/admin/mcq/questions/{question_id}", status_code=204)
async def delete_question(
    question_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    deleted = await mcq_service.delete_question(db, question_id)
    if not deleted:
        raise AppException(404, "not_found", "Question not found.")


@router.post("/admin/mcq/questions/{question_id}/approve", response_model=MCQQuestionOut)
async def approve_question(
    question_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    q = await mcq_service.approve_question(db, question_id)
    if not q:
        raise AppException(404, "not_found", "Question not found.")
    return MCQQuestionOut.model_validate(q)


@router.post("/admin/mcq/questions/{question_id}/unapprove", response_model=MCQQuestionOut)
async def unapprove_question(
    question_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    q = await mcq_service.unapprove_question(db, question_id)
    if not q:
        raise AppException(404, "not_found", "Question not found.")
    return MCQQuestionOut.model_validate(q)
