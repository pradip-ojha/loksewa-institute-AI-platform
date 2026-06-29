"""Personalization layer (CLAUDE.md §Personalization, spec §4).

Global-per-student artifacts that give the tutors compact-but-complete context. All
tables key on `student_id` only (NEVER per exam — personalization is holistic across all
enrolled exams).

Revision ID: 016
Revises: 015
Create Date: 2026-06-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Overall student intro — built from profile + activity, refreshed weekly.
    op.create_table(
        "student_profiles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("student_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("intro_text", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    # Raw activity records (objective/subjective tests). Raw detail is day-scoped:
    # the nightly beat distills + clears `raw_context` for prior days.
    op.create_table(
        "student_activity_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("student_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("activity_type", sa.String(40), nullable=False),   # mcq_test | subjective_test
        sa.Column("entity_type", sa.String(40), nullable=True),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=True),
        sa.Column("exam_id", UUID(as_uuid=True), nullable=True),
        sa.Column("activity_date", sa.Date, nullable=False),
        sa.Column("raw_context", JSONB, nullable=True),               # full detail (current day only)
        sa.Column("distilled", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("summary", sa.Text, nullable=True),                 # kept after raw_context is cleared
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_student_activity_logs_student", "student_activity_logs", ["student_id"])
    op.create_index("ix_student_activity_logs_date", "student_activity_logs", ["student_id", "activity_date"])

    # ONE rolling daily summary per student (all that day's chats + activities).
    op.create_table(
        "student_daily_summaries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("student_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("summary_text", sa.Text, nullable=False, server_default=""),
        sa.Column("summary_date", sa.Date, nullable=True),
        sa.Column("qa_since_update", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    # Weekly performance + key questions (one row per student per week).
    op.create_table(
        "student_weekly_summaries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("student_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("week_start", sa.Date, nullable=False),
        sa.Column("summary_text", sa.Text, nullable=False, server_default=""),
        sa.Column("key_questions", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("student_id", "week_start", name="uq_student_week"),
    )
    op.create_index("ix_student_weekly_summaries_student", "student_weekly_summaries", ["student_id"])

    # Per chat session across ALL chatbot types (tutor / video / subjective feedback).
    op.create_table(
        "chat_session_summaries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("student_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_kind", sa.String(30), nullable=False),     # tutor | video | subjective_feedback
        sa.Column("session_id", UUID(as_uuid=True), nullable=False),
        sa.Column("summary_text", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("session_kind", "session_id", name="uq_chat_session_summary"),
    )
    op.create_index("ix_chat_session_summaries_student", "chat_session_summaries", ["student_id"])

    # Extended weekly SUBJECTIVE-mock summary (mistake kinds + questions asked).
    # Updated immediately after each new subjective test. Aggregates across all
    # subjective exams; lives on the global profile.
    op.create_table(
        "extended_subjective_summaries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("student_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("summary_text", sa.Text, nullable=False, server_default=""),
        sa.Column("mistake_kinds", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("extended_subjective_summaries")
    op.drop_index("ix_chat_session_summaries_student", table_name="chat_session_summaries")
    op.drop_table("chat_session_summaries")
    op.drop_index("ix_student_weekly_summaries_student", table_name="student_weekly_summaries")
    op.drop_table("student_weekly_summaries")
    op.drop_table("student_daily_summaries")
    op.drop_index("ix_student_activity_logs_date", table_name="student_activity_logs")
    op.drop_index("ix_student_activity_logs_student", table_name="student_activity_logs")
    op.drop_table("student_activity_logs")
    op.drop_table("student_profiles")
