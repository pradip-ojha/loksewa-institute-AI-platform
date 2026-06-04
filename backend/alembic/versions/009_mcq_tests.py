"""mcq test sets, attempts and answers

Revision ID: 009
Revises: 008
Create Date: 2026-06-04

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mcq_test_blueprints",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("test_name", sa.String(255), nullable=False),
        sa.Column("total_time_minutes", sa.Integer, nullable=False, server_default="60"),
        sa.Column("num_sets", sa.Integer, nullable=False, server_default="1"),
        sa.Column("topic_distribution", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("difficulty_distribution", JSONB, nullable=True),
        sa.Column("custom_instruction", sa.Text, nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("generation_result", JSONB, nullable=True),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("processing_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_mcq_test_blueprints_status", "mcq_test_blueprints", ["status"])

    op.create_table(
        "mcq_test_sets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("blueprint_id", UUID(as_uuid=True), sa.ForeignKey("mcq_test_blueprints.id", ondelete="CASCADE"), nullable=False),
        sa.Column("set_name", sa.String(255), nullable=False),
        sa.Column("num_questions", sa.Integer, nullable=False, server_default="0"),
        sa.Column("difficulty_mix", JSONB, nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_mcq_test_sets_blueprint_id", "mcq_test_sets", ["blueprint_id"])
    op.create_index("ix_mcq_test_sets_status", "mcq_test_sets", ["status"])

    op.create_table(
        "mcq_test_set_questions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("set_id", UUID(as_uuid=True), sa.ForeignKey("mcq_test_sets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_id", UUID(as_uuid=True), sa.ForeignKey("mcq_questions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_order", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index("ix_mcq_test_set_questions_set_id", "mcq_test_set_questions", ["set_id"])
    op.create_unique_constraint("uq_set_question", "mcq_test_set_questions", ["set_id", "question_id"])

    op.create_table(
        "mcq_attempts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("set_id", UUID(as_uuid=True), sa.ForeignKey("mcq_test_sets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("score", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_questions", sa.Integer, nullable=False, server_default="0"),
        sa.Column("correct_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("time_taken_seconds", sa.Integer, nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="in_progress"),
    )
    op.create_index("ix_mcq_attempts_set_id", "mcq_attempts", ["set_id"])
    op.create_index("ix_mcq_attempts_student_id", "mcq_attempts", ["student_id"])
    # One attempt per (student, set) — enforces "no retake" at the DB level.
    op.create_unique_constraint("uq_attempt_student_set", "mcq_attempts", ["set_id", "student_id"])

    op.create_table(
        "mcq_attempt_answers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("attempt_id", UUID(as_uuid=True), sa.ForeignKey("mcq_attempts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_id", UUID(as_uuid=True), sa.ForeignKey("mcq_questions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("selected_option_id", sa.String(10), nullable=True),
        sa.Column("is_correct", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )
    op.create_index("ix_mcq_attempt_answers_attempt_id", "mcq_attempt_answers", ["attempt_id"])


def downgrade() -> None:
    op.drop_table("mcq_attempt_answers")
    op.drop_table("mcq_attempts")
    op.drop_table("mcq_test_set_questions")
    op.drop_table("mcq_test_sets")
    op.drop_table("mcq_test_blueprints")
