"""subjective tests + answer-sheet checking pipeline tables

Revision ID: 010
Revises: 009
Create Date: 2026-06-04

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "subjective_tests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("total_time_minutes", sa.Integer, nullable=False, server_default="60"),
        sa.Column("num_questions", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_marks", sa.Integer, nullable=False, server_default="0"),
        sa.Column("question_paper_file_id", UUID(as_uuid=True), sa.ForeignKey("files.id", ondelete="SET NULL"), nullable=True),
        sa.Column("model_answer_file_id", UUID(as_uuid=True), sa.ForeignKey("files.id", ondelete="SET NULL"), nullable=True),
        sa.Column("sample_marked_file_id", UUID(as_uuid=True), sa.ForeignKey("files.id", ondelete="SET NULL"), nullable=True),
        sa.Column("rubric_file_id", UUID(as_uuid=True), sa.ForeignKey("files.id", ondelete="SET NULL"), nullable=True),
        sa.Column("custom_instruction", sa.Text, nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("skill_generation_status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("skill_generation_job_id", UUID(as_uuid=True), sa.ForeignKey("processing_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_subjective_tests_status", "subjective_tests", ["status"])

    op.create_table(
        "subjective_questions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("test_id", UUID(as_uuid=True), sa.ForeignKey("subjective_tests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_number", sa.String(30), nullable=False),
        sa.Column("question_text", sa.Text, nullable=False),
        sa.Column("marks", sa.Integer, nullable=False, server_default="0"),
        sa.Column("question_order", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index("ix_subjective_questions_test_id", "subjective_questions", ["test_id"])

    op.create_table(
        "question_specific_checking_skills",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("test_id", UUID(as_uuid=True), sa.ForeignKey("subjective_tests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_id", UUID(as_uuid=True), sa.ForeignKey("subjective_questions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("skill_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_qscs_test_id", "question_specific_checking_skills", ["test_id"])
    op.create_index("ix_qscs_question_id", "question_specific_checking_skills", ["question_id"])

    op.create_table(
        "student_answer_sheets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("test_id", UUID(as_uuid=True), sa.ForeignKey("subjective_tests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_id", UUID(as_uuid=True), sa.ForeignKey("files.id", ondelete="SET NULL"), nullable=True),
        sa.Column("upload_attempt_number", sa.Integer, nullable=False, server_default="1"),
        sa.Column("current_status", sa.String(30), nullable=False, server_default="uploaded"),
        sa.Column("checking_job_id", UUID(as_uuid=True), sa.ForeignKey("processing_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_student_answer_sheets_test_id", "student_answer_sheets", ["test_id"])
    op.create_index("ix_student_answer_sheets_student_id", "student_answer_sheets", ["student_id"])

    op.create_table(
        "answer_quality_checks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("sheet_id", UUID(as_uuid=True), sa.ForeignKey("student_answer_sheets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("blur_score", sa.Float, nullable=True),
        sa.Column("brightness_score", sa.Float, nullable=True),
        sa.Column("tilt_angle", sa.Float, nullable=True),
        sa.Column("resolution_ok", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("readability_score", sa.Float, nullable=True),
        sa.Column("overall_status", sa.String(20), nullable=False, server_default="ok"),
        sa.Column("quality_notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_answer_quality_checks_sheet_id", "answer_quality_checks", ["sheet_id"])

    op.create_table(
        "answer_extractions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("sheet_id", UUID(as_uuid=True), sa.ForeignKey("student_answer_sheets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("extracted_data", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("overall_confidence", sa.Float, nullable=True),
        sa.Column("model_used", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_answer_extractions_sheet_id", "answer_extractions", ["sheet_id"])

    op.create_table(
        "answer_evaluations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("sheet_id", UUID(as_uuid=True), sa.ForeignKey("student_answer_sheets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evaluation_data", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("initial_evaluation_data", JSONB, nullable=True),
        sa.Column("reviewed", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("review_notes", sa.Text, nullable=True),
        sa.Column("total_marks_awarded", sa.Float, nullable=False, server_default="0"),
        sa.Column("total_marks_possible", sa.Float, nullable=False, server_default="0"),
        sa.Column("overall_confidence", sa.Float, nullable=True),
        sa.Column("model_used", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_answer_evaluations_sheet_id", "answer_evaluations", ["sheet_id"])

    op.create_table(
        "pdf_annotations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("sheet_id", UUID(as_uuid=True), sa.ForeignKey("student_answer_sheets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("annotation_instructions", JSONB, nullable=True),
        sa.Column("checked_file_id", UUID(as_uuid=True), sa.ForeignKey("files.id", ondelete="SET NULL"), nullable=True),
        sa.Column("annotation_status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_pdf_annotations_sheet_id", "pdf_annotations", ["sheet_id"])


def downgrade() -> None:
    op.drop_table("pdf_annotations")
    op.drop_table("answer_evaluations")
    op.drop_table("answer_extractions")
    op.drop_table("answer_quality_checks")
    op.drop_table("student_answer_sheets")
    op.drop_table("question_specific_checking_skills")
    op.drop_table("subjective_questions")
    op.drop_table("subjective_tests")
