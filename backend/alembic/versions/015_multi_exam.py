"""Multi-exam restructure: exam_type → exam → chapter → topic → subtopic.

Introduces the `exams` + `student_exam_enrollments` tables and makes `exam_id` the
universal routing key (replacing the binary `content_usage_type` / `syllabus_type`).

CLEAN SLATE (per upgrade decision): existing demo content cannot be backfilled with a
real `exam_id`, so all content/activity tables are TRUNCATEd here and re-created under
admin-defined exams. Pinecone must be wiped + knowledge re-uploaded separately (the
old vectors have no `exam_id` metadata).

Revision ID: 015
Revises: 014
Create Date: 2026-06-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tables that receive a NOT NULL exam_id FK (resource + main-tutor session tables).
_EXAM_ID_TABLES = (
    "syllabus_items",
    "knowledge_documents",
    "knowledge_chunks",
    "mcq_documents",
    "mcq_questions",
    "mcq_test_blueprints",
    "mcq_test_sets",
    "subjective_tests",
    "videos",
    "tutor_chat_sessions",
)

# Content/activity tables wiped for the clean-slate cut-over. Order is irrelevant —
# TRUNCATE ... CASCADE clears dependents. Users / files / skills / jobs / ai_audit are
# intentionally preserved.
_WIPE_TABLES = (
    "syllabus_items",
    "knowledge_chunks", "knowledge_documents",
    "mcq_rejection_feedback", "mcq_questions", "mcq_review_batches", "mcq_documents",
    "mcq_attempt_answers", "mcq_attempts", "mcq_test_set_questions", "mcq_test_sets", "mcq_test_blueprints",
    "pdf_annotations", "answer_evaluations", "answer_extractions", "answer_quality_checks",
    "student_answer_sheets", "question_specific_checking_skills", "subjective_questions", "subjective_tests",
    "subjective_feedback_messages", "subjective_feedback_chats",
    "video_chat_messages", "video_chat_sessions", "video_views", "video_slide_labels",
    "video_support_slides", "video_summaries", "video_timeline_segments", "video_transcripts",
    "video_audio_chunks", "videos",
    "tutor_chat_messages", "tutor_chat_sessions",
)


def upgrade() -> None:
    # ── New hierarchy tables ────────────────────────────────────────────────
    op.create_table(
        "exams",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("exam_type", sa.String(20), nullable=False),  # objective | subjective
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_exams_exam_type", "exams", ["exam_type"])

    op.create_table(
        "student_exam_enrollments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("student_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("exam_id", UUID(as_uuid=True), sa.ForeignKey("exams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("student_id", "exam_id", name="uq_student_exam"),
    )
    op.create_index("ix_student_exam_enrollments_student", "student_exam_enrollments", ["student_id"])

    # ── Clean-slate wipe of existing content (no real exam_id to backfill) ───
    op.execute("TRUNCATE TABLE " + ", ".join(_WIPE_TABLES) + " CASCADE")

    # ── Drop the old routing key ─────────────────────────────────────────────
    op.execute("ALTER TABLE knowledge_documents DROP CONSTRAINT IF EXISTS ck_knowledge_documents_content_usage_type")
    op.drop_column("knowledge_documents", "content_usage_type")
    op.drop_column("videos", "content_usage_type")
    op.drop_column("syllabus_items", "syllabus_type")
    op.execute("DROP TYPE IF EXISTS syllabus_type")

    # ── Add exam_id (NOT NULL, safe because tables were just truncated) ──────
    for table in _EXAM_ID_TABLES:
        op.add_column(table, sa.Column("exam_id", UUID(as_uuid=True), nullable=False))
        op.create_foreign_key(f"fk_{table}_exam", table, "exams", ["exam_id"], ["id"])
        op.create_index(f"ix_{table}_exam_id", table, ["exam_id"])

    # Knowledge documents now carry a real admin-defined chapter (the hardcoded
    # chapter-by-usage-type logic is removed).
    op.add_column("knowledge_documents", sa.Column("chapter", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("knowledge_documents", "chapter")
    for table in _EXAM_ID_TABLES:
        op.drop_index(f"ix_{table}_exam_id", table_name=table)
        op.drop_constraint(f"fk_{table}_exam", table, type_="foreignkey")
        op.drop_column(table, "exam_id")

    syllabus_type = sa.Enum("objective", "subjective", name="syllabus_type")
    syllabus_type.create(op.get_bind(), checkfirst=True)
    op.add_column("syllabus_items", sa.Column("syllabus_type", syllabus_type, nullable=False, server_default="objective"))
    op.add_column("videos", sa.Column("content_usage_type", sa.String(20), nullable=False, server_default="objective"))
    op.add_column("knowledge_documents", sa.Column("content_usage_type", sa.String(50), nullable=False, server_default="objective"))

    op.drop_index("ix_student_exam_enrollments_student", table_name="student_exam_enrollments")
    op.drop_table("student_exam_enrollments")
    op.drop_index("ix_exams_exam_type", table_name="exams")
    op.drop_table("exams")
