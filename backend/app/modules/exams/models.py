"""Multi-exam hierarchy (CLAUDE.md §1/§7): exam_type → exam → chapter → topic → subtopic.

`exam_id` is the universal routing key that replaced the old binary `content_usage_type`
(objective|subjective) string. Each exam is strictly ONE type (objective OR subjective) — an
exam paper that has both becomes two exam rows. `exam_type` is derived from the exam row, never
stored redundantly on resources.

`student_exam_enrollments` is admin-managed (Students UI); student endpoints filter to the exams
a student is enrolled in.
"""
import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# Allowed exam types. Kept as a plain string column (not a DB enum) so new content
# verticals never require a migration — the API validates against this tuple.
EXAM_TYPES = ("objective", "subjective")


class Exam(Base):
    __tablename__ = "exams"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # objective | subjective — fixed per row (one type per exam).
    exam_type: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")  # active | archived
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class StudentExamEnrollment(Base):
    __tablename__ = "student_exam_enrollments"
    __table_args__ = (UniqueConstraint("student_id", "exam_id", name="uq_student_exam"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    exam_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("exams.id", ondelete="CASCADE"), nullable=False)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
