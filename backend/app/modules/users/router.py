import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, require_admin
from app.core.database import get_db
from app.core.exceptions import AppException
from app.core.security import hash_password, verify_password
from app.modules.users.models import User, UserRole, UserStatus
from app.modules.users.schemas import UserCreate, UserOut, UserUpdate, PasswordReset
from pydantic import BaseModel

router = APIRouter(tags=["users"])


# ── Admin: student management ─────────────────────────────────────────────────

class StudentListOut(BaseModel):
    items: list[UserOut]
    total: int
    page: int
    per_page: int


@router.get("/admin/students", response_model=StudentListOut)
async def list_students(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: str = Query(""),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    base = select(User).where(User.role == UserRole.student)
    if search:
        pattern = f"%{search}%"
        base = base.where(
            User.full_name.ilike(pattern) | User.email.ilike(pattern)
        )
    total_result = await db.execute(select(func.count()).select_from(base.subquery()))
    total = total_result.scalar_one()

    result = await db.execute(
        base.order_by(User.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    students = result.scalars().all()
    return StudentListOut(
        items=[UserOut.model_validate(s) for s in students],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.post("/admin/students", response_model=UserOut, status_code=201)
async def create_student(
    payload: UserCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none():
        raise AppException(409, "email_taken", "A user with this email already exists.")
    if len(payload.password) < 6:
        raise AppException(422, "weak_password", "Password must be at least 6 characters.")

    student = User(
        full_name=payload.full_name,
        email=payload.email,
        password_hash=hash_password(payload.password),
        phone=payload.phone,
        role=UserRole.student,
        status=UserStatus.active,
    )
    db.add(student)
    await db.commit()
    await db.refresh(student)
    return UserOut.model_validate(student)


@router.put("/admin/students/{student_id}", response_model=UserOut)
async def update_student(
    student_id: uuid.UUID,
    payload: UserUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(User).where(User.id == student_id, User.role == UserRole.student))
    student = result.scalar_one_or_none()
    if not student:
        raise AppException(404, "not_found", "Student not found.")
    if payload.full_name is not None:
        student.full_name = payload.full_name
    if payload.phone is not None:
        student.phone = payload.phone
    await db.commit()
    await db.refresh(student)
    return UserOut.model_validate(student)


@router.post("/admin/students/{student_id}/deactivate", response_model=UserOut)
async def deactivate_student(
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(User).where(User.id == student_id, User.role == UserRole.student))
    student = result.scalar_one_or_none()
    if not student:
        raise AppException(404, "not_found", "Student not found.")
    student.status = UserStatus.inactive
    await db.commit()
    await db.refresh(student)
    return UserOut.model_validate(student)


@router.post("/admin/students/{student_id}/activate", response_model=UserOut)
async def activate_student(
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(User).where(User.id == student_id, User.role == UserRole.student))
    student = result.scalar_one_or_none()
    if not student:
        raise AppException(404, "not_found", "Student not found.")
    student.status = UserStatus.active
    await db.commit()
    await db.refresh(student)
    return UserOut.model_validate(student)


@router.post("/admin/students/{student_id}/reset-password", response_model=UserOut)
async def reset_student_password(
    student_id: uuid.UUID,
    payload: PasswordReset,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(User).where(User.id == student_id, User.role == UserRole.student))
    student = result.scalar_one_or_none()
    if not student:
        raise AppException(404, "not_found", "Student not found.")
    if len(payload.new_password) < 6:
        raise AppException(422, "weak_password", "Password must be at least 6 characters.")
    student.password_hash = hash_password(payload.new_password)
    await db.commit()
    await db.refresh(student)
    return UserOut.model_validate(student)


# ── Student: own profile ──────────────────────────────────────────────────────

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.get("/student/profile", response_model=UserOut)
async def get_profile(current_user: User = Depends(get_current_user)):
    return UserOut.model_validate(current_user)


@router.put("/student/profile/password")
async def change_password(
    payload: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not verify_password(payload.current_password, current_user.password_hash):
        raise AppException(400, "wrong_password", "Current password is incorrect.")
    if len(payload.new_password) < 6:
        raise AppException(422, "weak_password", "New password must be at least 6 characters.")
    current_user.password_hash = hash_password(payload.new_password)
    await db.commit()
    return {"message": "Password changed successfully."}


# ── Admin: dashboard stats ────────────────────────────────────────────────────

class DashboardStats(BaseModel):
    total_students: int
    total_active_students: int
    total_knowledge_documents: int
    total_approved_mcqs: int
    total_active_mcq_sets: int
    total_subjective_tests: int
    total_videos: int
    pending_jobs: int
    failed_jobs: int


@router.get("/admin/dashboard/stats", response_model=DashboardStats)
async def dashboard_stats(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    from app.modules.jobs.models import ProcessingJob, JobStatus
    from sqlalchemy import text as sa_text

    total = await db.execute(select(func.count()).where(User.role == UserRole.student))
    active = await db.execute(
        select(func.count()).where(User.role == UserRole.student, User.status == UserStatus.active)
    )
    pending = await db.execute(
        select(func.count()).select_from(ProcessingJob).where(
            ProcessingJob.status.in_([JobStatus.queued, JobStatus.processing, JobStatus.retrying])
        )
    )
    failed = await db.execute(
        select(func.count()).select_from(ProcessingJob).where(ProcessingJob.status == JobStatus.failed)
    )

    # These counts return 0 until the relevant tables exist (phases 5+)
    def _safe_count(q):
        try:
            return q
        except Exception:
            return 0

    knowledge_count = 0
    mcq_count = 0
    active_sets_count = 0
    subjective_count = 0
    video_count = 0

    try:
        from app.modules.knowledge.models import KnowledgeDocument
        r = await db.execute(select(func.count()).select_from(KnowledgeDocument))
        knowledge_count = r.scalar_one()
    except Exception:
        pass

    try:
        from app.modules.mcq.models import MCQQuestion
        r = await db.execute(select(func.count()).select_from(MCQQuestion).where(MCQQuestion.status == "approved"))
        mcq_count = r.scalar_one()
    except Exception:
        pass

    try:
        from app.modules.mcq_tests.models import MCQTestSet
        r = await db.execute(select(func.count()).select_from(MCQTestSet).where(MCQTestSet.status == "active"))
        active_sets_count = r.scalar_one()
    except Exception:
        pass

    try:
        from app.modules.subjective.models import SubjectiveTest
        r = await db.execute(select(func.count()).select_from(SubjectiveTest))
        subjective_count = r.scalar_one()
    except Exception:
        pass

    try:
        from app.modules.video_tutor.models import Video
        r = await db.execute(select(func.count()).select_from(Video))
        video_count = r.scalar_one()
    except Exception:
        pass

    return DashboardStats(
        total_students=total.scalar_one(),
        total_active_students=active.scalar_one(),
        total_knowledge_documents=knowledge_count,
        total_approved_mcqs=mcq_count,
        total_active_mcq_sets=active_sets_count,
        total_subjective_tests=subjective_count,
        total_videos=video_count,
        pending_jobs=pending.scalar_one(),
        failed_jobs=failed.scalar_one(),
    )
