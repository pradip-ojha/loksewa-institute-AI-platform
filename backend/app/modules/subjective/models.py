import uuid
from datetime import datetime

from sqlalchemy import String, Integer, Float, DateTime, ForeignKey, Text, Boolean, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SubjectiveTest(Base):
    """An admin-configured subjective test. The answer-sheet checking pipeline
    reads everything it needs (question paper, per-question marks, optional
    rubric file, optional checking instruction) from this row and its
    questions — nothing about marking is hardcoded in the pipeline."""

    __tablename__ = "subjective_tests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    total_time_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    num_questions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_marks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    question_paper_file_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"), nullable=True)
    model_answer_file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"), nullable=True)
    sample_marked_file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"), nullable=True)
    # Optional per-test rubric file. When null, the checker uses the default rubric.
    rubric_file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"), nullable=True)
    custom_instruction: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")  # draft | active | archived
    # Lifecycle of the auto question-paper extraction + per-question skill generation.
    skill_generation_status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")  # pending | processing | completed | failed
    skill_generation_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("processing_jobs.id", ondelete="SET NULL"), nullable=True)

    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SubjectiveQuestion(Base):
    """A question extracted from the test's question paper. Marks here are the
    source of truth for the maximum awardable per question."""

    __tablename__ = "subjective_questions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    test_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("subjective_tests.id", ondelete="CASCADE"), nullable=False)
    question_number: Mapped[str] = mapped_column(String(30), nullable=False)  # e.g. "Q1", "प्रश्न नं. १"
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    marks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    question_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class QuestionSpecificCheckingSkill(Base):
    """Auto-generated, internal per-question checking guide (no admin approval).
    Used by the evaluator as enrichment on top of the live test config."""

    __tablename__ = "question_specific_checking_skills"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    test_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("subjective_tests.id", ondelete="CASCADE"), nullable=False)
    question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("subjective_questions.id", ondelete="CASCADE"), nullable=False)
    skill_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class StudentAnswerSheet(Base):
    """One student's uploaded answer sheet for a test. Re-uploads create new
    rows with an incremented attempt number (quality-gate allows up to 2)."""

    __tablename__ = "student_answer_sheets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    test_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("subjective_tests.id", ondelete="CASCADE"), nullable=False)
    student_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    file_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"), nullable=True)
    upload_attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # uploaded | quality_check | needs_reupload | extracting | evaluating | reviewing | annotating | checked | failed
    current_status: Mapped[str] = mapped_column(String(30), nullable=False, default="uploaded")
    checking_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("processing_jobs.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AnswerQualityCheck(Base):
    __tablename__ = "answer_quality_checks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sheet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("student_answer_sheets.id", ondelete="CASCADE"), nullable=False)
    blur_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    brightness_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    tilt_angle: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolution_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    readability_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    overall_status: Mapped[str] = mapped_column(String(20), nullable=False, default="ok")  # ok | warn | poor
    quality_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AnswerExtraction(Base):
    __tablename__ = "answer_extractions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sheet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("student_answer_sheets.id", ondelete="CASCADE"), nullable=False)
    # Full line-level extraction: {"pages": [...], "questions": [{qid, lines:[{id,text,bbox,page}]}]}
    extracted_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    overall_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_used: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AnswerEvaluation(Base):
    __tablename__ = "answer_evaluations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sheet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("student_answer_sheets.id", ondelete="CASCADE"), nullable=False)
    # The REVIEWED evaluation used for the checked PDF + result: {"questions": [{qid, m, fm, fb, mistakes, ann}]}
    evaluation_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # The pre-review evaluation, kept for audit.
    initial_evaluation_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_marks_awarded: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    total_marks_possible: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    overall_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_used: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PDFAnnotation(Base):
    __tablename__ = "pdf_annotations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sheet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("student_answer_sheets.id", ondelete="CASCADE"), nullable=False)
    annotation_instructions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    checked_file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"), nullable=True)
    annotation_status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")  # pending | completed | failed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
