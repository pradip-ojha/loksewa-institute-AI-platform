import uuid
from datetime import datetime

from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.mcq.models import MCQDocument, MCQReviewBatch, MCQQuestion, MCQRejectionFeedback

_MCQ_DOC_TERMINAL = ("completed", "failed")


async def fail_orphaned_mcq_documents(db: AsyncSession) -> int:
    """Mark in-progress MCQ documents as failed when no live job references them.

    A worker that died mid-extraction/generation leaves the job failed (by the reaper /
    startup recovery) but the document's `processing_status` stuck, so the admin view
    spins forever. MCQ docs have no job FK, so we check for any still-live job via
    `input_reference->>'document_id'`; if none, the document is orphaned → 'failed'.
    Returns how many were reconciled."""
    from app.modules.jobs.service import has_live_job_for_document

    reconciled = 0
    docs = (await db.execute(
        select(MCQDocument).where(~MCQDocument.processing_status.in_(_MCQ_DOC_TERMINAL))
    )).scalars().all()
    for doc in docs:
        if not await has_live_job_for_document(db, doc.id):
            doc.processing_status = "failed"
            reconciled += 1
    if reconciled:
        await db.commit()
    return reconciled


async def get_document(db: AsyncSession, doc_id: uuid.UUID) -> MCQDocument | None:
    result = await db.execute(select(MCQDocument).where(MCQDocument.id == doc_id))
    return result.scalar_one_or_none()


async def list_documents(db: AsyncSession, page: int = 1, per_page: int = 20) -> tuple[list[MCQDocument], int]:
    total_r = await db.execute(select(func.count()).select_from(MCQDocument))
    total = total_r.scalar_one()
    result = await db.execute(
        select(MCQDocument).order_by(MCQDocument.created_at.desc())
        .offset((page - 1) * per_page).limit(per_page)
    )
    return result.scalars().all(), total


async def get_batch(db: AsyncSession, batch_id: uuid.UUID) -> MCQReviewBatch | None:
    result = await db.execute(select(MCQReviewBatch).where(MCQReviewBatch.id == batch_id))
    return result.scalar_one_or_none()


async def list_batches(db: AsyncSession, page: int = 1, per_page: int = 20) -> tuple[list[MCQReviewBatch], int]:
    total_r = await db.execute(select(func.count()).select_from(MCQReviewBatch))
    total = total_r.scalar_one()
    result = await db.execute(
        select(MCQReviewBatch).order_by(MCQReviewBatch.created_at.desc())
        .offset((page - 1) * per_page).limit(per_page)
    )
    return result.scalars().all(), total


async def get_batch_questions(db: AsyncSession, batch_id: uuid.UUID) -> list[MCQQuestion]:
    result = await db.execute(
        select(MCQQuestion).where(MCQQuestion.review_batch_id == batch_id)
        .order_by(MCQQuestion.created_at)
    )
    return result.scalars().all()


async def list_questions(
    db: AsyncSession,
    status: str | None = None,
    topic: str | None = None,
    subtopic: str | None = None,
    complexity: str | None = None,
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[MCQQuestion], int]:
    q = select(MCQQuestion)
    if status:
        q = q.where(MCQQuestion.status == status)
    if topic:
        q = q.where(MCQQuestion.topic == topic)
    if subtopic:
        q = q.where(MCQQuestion.subtopic == subtopic)
    if complexity:
        q = q.where(MCQQuestion.complexity == complexity)

    total_r = await db.execute(select(func.count()).select_from(q.subquery()))
    total = total_r.scalar_one()
    result = await db.execute(q.order_by(MCQQuestion.created_at.desc()).offset((page - 1) * per_page).limit(per_page))
    return result.scalars().all(), total


async def _count_in_batch(db: AsyncSession, batch_id: uuid.UUID, status: str) -> int:
    r = await db.execute(
        select(func.count()).select_from(MCQQuestion).where(
            MCQQuestion.review_batch_id == batch_id,
            MCQQuestion.status == status,
        )
    )
    return r.scalar_one()


async def accept_question(db: AsyncSession, question_id: uuid.UUID) -> MCQQuestion | None:
    result = await db.execute(select(MCQQuestion).where(MCQQuestion.id == question_id))
    q = result.scalar_one_or_none()
    # Idempotent: re-accepting an already-approved question is a no-op so a
    # retried request doesn't churn updated_at or counts.
    if q and q.status != "approved":
        q.status = "approved"
        q.updated_at = datetime.utcnow()
        # Keep the batch counters in sync with per-question reviews too — not
        # only the bulk paths (autoflush makes the change above visible).
        if q.review_batch_id:
            batch_r = await db.execute(select(MCQReviewBatch).where(MCQReviewBatch.id == q.review_batch_id))
            batch = batch_r.scalar_one_or_none()
            if batch:
                batch.accepted_count = await _count_in_batch(db, q.review_batch_id, "approved")
                batch.rejected_count = await _count_in_batch(db, q.review_batch_id, "rejected")
        await db.commit()
        await db.refresh(q)
    return q


async def reject_question(
    db: AsyncSession, question_id: uuid.UUID, feedback: str
) -> tuple[MCQQuestion | None, bool]:
    """Returns (question, changed). `changed` is True only when this call moved
    the question into the rejected state, so the caller can avoid double-firing
    side effects (feedback rows, skill-update jobs) on retries."""
    result = await db.execute(select(MCQQuestion).where(MCQQuestion.id == question_id))
    q = result.scalar_one_or_none()
    if not q:
        return None, False
    changed = q.status != "rejected"
    if changed or q.review_feedback != feedback:
        q.status = "rejected"
        q.review_feedback = feedback
        q.updated_at = datetime.utcnow()
        if changed and q.review_batch_id:
            batch_r = await db.execute(select(MCQReviewBatch).where(MCQReviewBatch.id == q.review_batch_id))
            batch = batch_r.scalar_one_or_none()
            if batch:
                batch.accepted_count = await _count_in_batch(db, q.review_batch_id, "approved")
                batch.rejected_count = await _count_in_batch(db, q.review_batch_id, "rejected")
        await db.commit()
        await db.refresh(q)
    return q, changed


async def bulk_accept_batch(db: AsyncSession, batch_id: uuid.UUID) -> int:
    result = await db.execute(
        select(MCQQuestion).where(MCQQuestion.review_batch_id == batch_id, MCQQuestion.status == "draft")
    )
    questions = result.scalars().all()
    for q in questions:
        q.status = "approved"
        q.updated_at = datetime.utcnow()
    batch_r = await db.execute(select(MCQReviewBatch).where(MCQReviewBatch.id == batch_id))
    batch = batch_r.scalar_one_or_none()
    if batch:
        # Derive the count from actual approved rows (autoflush makes the changes
        # above visible) so a re-run can't reset the count to 0.
        batch.accepted_count = await _count_in_batch(db, batch_id, "approved")
        batch.status = "completed"
    await db.commit()
    return len(questions)


async def bulk_reject_batch(db: AsyncSession, batch_id: uuid.UUID, feedback: str) -> tuple[list[MCQQuestion], int]:
    result = await db.execute(
        select(MCQQuestion).where(MCQQuestion.review_batch_id == batch_id, MCQQuestion.status == "draft")
    )
    questions = result.scalars().all()
    for q in questions:
        q.status = "rejected"
        q.review_feedback = feedback
        q.updated_at = datetime.utcnow()
    batch_r = await db.execute(select(MCQReviewBatch).where(MCQReviewBatch.id == batch_id))
    batch = batch_r.scalar_one_or_none()
    if batch:
        batch.rejected_count = await _count_in_batch(db, batch_id, "rejected")
        # Only record feedback + flip status when this call actually rejected
        # something; a retried reject-all (0 drafts left) leaves no duplicate row.
        if questions:
            batch.rejection_feedback = feedback
            batch.status = "in_review"
            db.add(MCQRejectionFeedback(batch_id=batch_id, feedback_text=feedback, created_by=batch.created_by))
    await db.commit()
    return questions, len(questions)


async def approve_question(db: AsyncSession, question_id: uuid.UUID) -> MCQQuestion | None:
    result = await db.execute(select(MCQQuestion).where(MCQQuestion.id == question_id))
    q = result.scalar_one_or_none()
    if q:
        q.status = "approved"
        q.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(q)
    return q


async def unapprove_question(db: AsyncSession, question_id: uuid.UUID) -> MCQQuestion | None:
    result = await db.execute(select(MCQQuestion).where(MCQQuestion.id == question_id))
    q = result.scalar_one_or_none()
    if q:
        q.status = "draft"
        q.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(q)
    return q


async def delete_question(db: AsyncSession, question_id: uuid.UUID) -> bool:
    result = await db.execute(select(MCQQuestion).where(MCQQuestion.id == question_id))
    q = result.scalar_one_or_none()
    if q:
        await db.delete(q)
        await db.commit()
        return True
    return False
