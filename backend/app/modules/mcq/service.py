import uuid
from datetime import datetime

from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.mcq.models import MCQDocument, MCQReviewBatch, MCQQuestion, MCQRejectionFeedback


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


async def accept_question(db: AsyncSession, question_id: uuid.UUID) -> MCQQuestion | None:
    result = await db.execute(select(MCQQuestion).where(MCQQuestion.id == question_id))
    q = result.scalar_one_or_none()
    if q:
        q.status = "approved"
        q.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(q)
    return q


async def reject_question(db: AsyncSession, question_id: uuid.UUID, feedback: str) -> MCQQuestion | None:
    result = await db.execute(select(MCQQuestion).where(MCQQuestion.id == question_id))
    q = result.scalar_one_or_none()
    if q:
        q.status = "rejected"
        q.review_feedback = feedback
        q.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(q)
    return q


async def bulk_accept_batch(db: AsyncSession, batch_id: uuid.UUID) -> int:
    result = await db.execute(
        select(MCQQuestion).where(MCQQuestion.review_batch_id == batch_id, MCQQuestion.status == "draft")
    )
    questions = result.scalars().all()
    for q in questions:
        q.status = "approved"
        q.updated_at = datetime.utcnow()
    # update batch counts
    batch_r = await db.execute(select(MCQReviewBatch).where(MCQReviewBatch.id == batch_id))
    batch = batch_r.scalar_one_or_none()
    if batch:
        batch.accepted_count = len(questions)
        batch.status = "completed"
    await db.commit()
    return len(questions)


async def bulk_reject_batch(db: AsyncSession, batch_id: uuid.UUID, feedback: str) -> int:
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
        batch.rejected_count = len(questions)
        batch.rejection_feedback = feedback
        batch.status = "in_review"
        # save feedback record
        db.add(MCQRejectionFeedback(batch_id=batch_id, feedback_text=feedback, created_by=batch.created_by))
    await db.commit()
    return len(questions)


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
