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
from pydantic import BaseModel, EmailStr

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


# ── Admin: exam enrollment ─────────────────────────────────────────────────────

class EnrollRequest(BaseModel):
    exam_id: uuid.UUID


@router.get("/admin/students/{student_id}/exams")
async def list_student_exams(
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    from app.modules.exams import service as exam_svc
    return await exam_svc.list_student_enrollments(db, student_id)


@router.post("/admin/students/{student_id}/exams", status_code=201)
async def enroll_student_in_exam(
    student_id: uuid.UUID,
    payload: EnrollRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(User).where(User.id == student_id, User.role == UserRole.student))
    if not result.scalar_one_or_none():
        raise AppException(404, "not_found", "Student not found.")
    from app.modules.exams import service as exam_svc
    await exam_svc.enroll_student(db, student_id=student_id, exam_id=payload.exam_id)
    return {"enrolled": True}


@router.delete("/admin/students/{student_id}/exams/{exam_id}", status_code=204)
async def unenroll_student_from_exam(
    student_id: uuid.UUID,
    exam_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    from app.modules.exams import service as exam_svc
    await exam_svc.unenroll_student(db, student_id=student_id, exam_id=exam_id)


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


# ── Admin: own profile (email + password) ─────────────────────────────────────

class ChangeEmailRequest(BaseModel):
    current_password: str
    new_email: EmailStr


@router.get("/admin/profile", response_model=UserOut)
async def get_admin_profile(current_admin: User = Depends(require_admin)):
    return UserOut.model_validate(current_admin)


@router.put("/admin/profile/password")
async def change_admin_password(
    payload: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(require_admin),
):
    if not verify_password(payload.current_password, current_admin.password_hash):
        raise AppException(400, "wrong_password", "Current password is incorrect.")
    if len(payload.new_password) < 6:
        raise AppException(422, "weak_password", "New password must be at least 6 characters.")
    current_admin.password_hash = hash_password(payload.new_password)
    await db.commit()
    return {"message": "Password changed successfully."}


@router.put("/admin/profile/email", response_model=UserOut)
async def change_admin_email(
    payload: ChangeEmailRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(require_admin),
):
    # Require the current password to change the login email (security-sensitive).
    if not verify_password(payload.current_password, current_admin.password_hash):
        raise AppException(400, "wrong_password", "Current password is incorrect.")
    new_email = payload.new_email.strip()
    if new_email == current_admin.email:
        raise AppException(422, "same_email", "The new email is the same as the current one.")
    existing = await db.execute(
        select(User).where(User.email.ilike(new_email), User.id != current_admin.id)
    )
    if existing.scalar_one_or_none():
        raise AppException(409, "email_taken", "A user with this email already exists.")
    current_admin.email = new_email
    await db.commit()
    await db.refresh(current_admin)
    return UserOut.model_validate(current_admin)
