"""Exam + enrollment service. The `exam_id` → `exam_type` resolution helpers here are
reused across knowledge/mcq/subjective/video/tutor so the routing key stays consistent."""
import uuid

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.modules.exams.models import EXAM_TYPES, Exam, StudentExamEnrollment


async def get_exam_or_404(db: AsyncSession, exam_id: uuid.UUID) -> Exam:
    exam = (await db.execute(select(Exam).where(Exam.id == exam_id))).scalar_one_or_none()
    if not exam:
        raise AppException(404, "exam_not_found", "Exam not found.")
    return exam


async def get_exam_type(db: AsyncSession, exam_id: uuid.UUID) -> str:
    """Resolve an exam's type (objective|subjective) — the value derived everywhere
    that used to read `content_usage_type`."""
    return (await get_exam_or_404(db, exam_id)).exam_type


async def create_exam(db: AsyncSession, *, exam_type: str, name: str, description: str | None, created_by: uuid.UUID) -> Exam:
    if exam_type not in EXAM_TYPES:
        raise AppException(422, "invalid_exam_type", "exam_type must be 'objective' or 'subjective'.")
    name = (name or "").strip()
    if not name:
        raise AppException(422, "invalid_name", "Exam name is required.")
    exam = Exam(id=uuid.uuid4(), exam_type=exam_type, name=name, description=(description or None), created_by=created_by)
    db.add(exam)
    await db.commit()
    await db.refresh(exam)
    return exam


async def list_exams(db: AsyncSession, *, exam_type: str | None = None, include_archived: bool = True) -> list[Exam]:
    stmt = select(Exam).order_by(Exam.created_at.desc())
    if exam_type:
        stmt = stmt.where(Exam.exam_type == exam_type)
    if not include_archived:
        stmt = stmt.where(Exam.status == "active")
    return list((await db.execute(stmt)).scalars().all())


async def update_exam(db: AsyncSession, exam_id: uuid.UUID, *, name: str | None, description: str | None, status: str | None) -> Exam:
    exam = await get_exam_or_404(db, exam_id)
    if name is not None:
        n = name.strip()
        if not n:
            raise AppException(422, "invalid_name", "Exam name cannot be empty.")
        exam.name = n
    if description is not None:
        exam.description = description or None
    if status is not None:
        if status not in ("active", "archived"):
            raise AppException(422, "invalid_status", "status must be 'active' or 'archived'.")
        exam.status = status
    await db.commit()
    await db.refresh(exam)
    return exam


# ── Enrollment ──────────────────────────────────────────────────────────────

async def enroll_student(db: AsyncSession, *, student_id: uuid.UUID, exam_id: uuid.UUID) -> None:
    await get_exam_or_404(db, exam_id)
    exists = (await db.execute(
        select(StudentExamEnrollment).where(
            StudentExamEnrollment.student_id == student_id,
            StudentExamEnrollment.exam_id == exam_id,
        )
    )).scalar_one_or_none()
    if exists:
        return
    db.add(StudentExamEnrollment(id=uuid.uuid4(), student_id=student_id, exam_id=exam_id))
    await db.commit()


async def unenroll_student(db: AsyncSession, *, student_id: uuid.UUID, exam_id: uuid.UUID) -> None:
    await db.execute(
        delete(StudentExamEnrollment).where(
            StudentExamEnrollment.student_id == student_id,
            StudentExamEnrollment.exam_id == exam_id,
        )
    )
    await db.commit()


async def get_enrolled_exam_ids(db: AsyncSession, student_id: uuid.UUID) -> list[uuid.UUID]:
    rows = (await db.execute(
        select(StudentExamEnrollment.exam_id).where(StudentExamEnrollment.student_id == student_id)
    )).scalars().all()
    return list(rows)


async def list_student_enrollments(db: AsyncSession, student_id: uuid.UUID) -> list[dict]:
    rows = (await db.execute(
        select(Exam, StudentExamEnrollment.enrolled_at)
        .join(StudentExamEnrollment, StudentExamEnrollment.exam_id == Exam.id)
        .where(StudentExamEnrollment.student_id == student_id)
        .order_by(StudentExamEnrollment.enrolled_at.desc())
    )).all()
    return [
        {"exam_id": exam.id, "exam_type": exam.exam_type, "name": exam.name, "enrolled_at": enrolled_at}
        for exam, enrolled_at in rows
    ]


async def ensure_enrolled(db: AsyncSession, *, student_id: uuid.UUID, exam_id: uuid.UUID) -> None:
    """Guard for student endpoints: 403 if the student is not enrolled in the exam."""
    ids = await get_enrolled_exam_ids(db, student_id)
    if exam_id not in ids:
        raise AppException(403, "not_enrolled", "You are not enrolled in this exam.")
