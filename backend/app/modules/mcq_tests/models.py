import uuid
from datetime import datetime

from sqlalchemy import String, Integer, DateTime, ForeignKey, Text, Boolean, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class MCQTestBlueprint(Base):
    """An admin-defined recipe for generating one or more MCQ test sets from the
    approved question pool. Set generation runs as a background job; `status`
    tracks that lifecycle (draft → generating → generated | shortage)."""

    __tablename__ = "mcq_test_blueprints"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    exam_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("exams.id", ondelete="CASCADE"), nullable=False)
    test_name: Mapped[str] = mapped_column(String(255), nullable=False)
    total_time_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    num_sets: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # [{"topic": "...", "subtopic": "..."|null, "count": int}]  — questions per set
    topic_distribution: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # {"easy": int, "medium": int, "hard": int}  — optional per-set difficulty mix
    difficulty_distribution: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    custom_instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    # Persisted result of the last generation attempt (sets_created / shortages).
    generation_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("processing_jobs.id", ondelete="SET NULL"), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MCQTestSet(Base):
    __tablename__ = "mcq_test_sets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    blueprint_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("mcq_test_blueprints.id", ondelete="CASCADE"), nullable=False)
    exam_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("exams.id", ondelete="CASCADE"), nullable=False)
    set_name: Mapped[str] = mapped_column(String(255), nullable=False)
    num_questions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    difficulty_mix: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")  # draft | active | archived
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MCQTestSetQuestion(Base):
    __tablename__ = "mcq_test_set_questions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    set_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("mcq_test_sets.id", ondelete="CASCADE"), nullable=False)
    question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("mcq_questions.id", ondelete="CASCADE"), nullable=False)
    question_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class MCQAttempt(Base):
    __tablename__ = "mcq_attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    set_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("mcq_test_sets.id", ondelete="CASCADE"), nullable=False)
    student_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_questions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    time_taken_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="in_progress")  # in_progress | submitted


class MCQAttemptAnswer(Base):
    __tablename__ = "mcq_attempt_answers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    attempt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("mcq_attempts.id", ondelete="CASCADE"), nullable=False)
    question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("mcq_questions.id", ondelete="CASCADE"), nullable=False)
    selected_option_id: Mapped[str | None] = mapped_column(String(10), nullable=True)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
