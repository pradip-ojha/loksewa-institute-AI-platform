import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_admin, require_student
from app.core.database import get_db
from app.modules.exams import service
from app.modules.exams.schemas import EnrollmentOut, ExamCreate, ExamOut, ExamUpdate
from app.modules.users.models import User

router = APIRouter(tags=["exams"])


# ── Admin: exam CRUD ─────────────────────────────────────────────────────────

@router.get("/admin/exams", response_model=list[ExamOut])
async def list_exams(
    exam_type: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    return await service.list_exams(db, exam_type=exam_type)


@router.post("/admin/exams", response_model=ExamOut, status_code=201)
async def create_exam(
    payload: ExamCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    return await service.create_exam(
        db, exam_type=payload.exam_type, name=payload.name,
        description=payload.description, created_by=admin.id,
    )


@router.put("/admin/exams/{exam_id}", response_model=ExamOut)
async def update_exam(
    exam_id: uuid.UUID,
    payload: ExamUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    return await service.update_exam(
        db, exam_id, name=payload.name, description=payload.description, status=payload.status,
    )


@router.delete("/admin/exams/{exam_id}", status_code=204)
async def delete_exam(
    exam_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> None:
    """Permanently delete an exam and ALL its content (syllabus, knowledge + vectors, MCQ,
    tests + attempts, subjective tests + submissions, videos, tutor chats, enrollments, and
    the uploaded files). Irreversible — the UI must confirm before calling this."""
    await service.delete_exam(db, exam_id)


# ── Student: enrolled exams ──────────────────────────────────────────────────

@router.get("/student/exams", response_model=list[EnrollmentOut])
async def my_exams(
    db: AsyncSession = Depends(get_db),
    student: User = Depends(require_student),
):
    return await service.list_student_enrollments(db, student.id)
