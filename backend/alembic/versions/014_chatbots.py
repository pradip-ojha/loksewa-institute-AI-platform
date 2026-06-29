"""answer-sheet feedback chatbot + standalone AI tutor chat tables

Revision ID: 014
Revises: 013
Create Date: 2026-06-15

Adds the persistence for two new synchronous student chat features:
  • subjective_feedback_chats / subjective_feedback_messages — a follow-up chatbot
    that explains an already-checked answer sheet (it never re-grades; it reads the
    stored evaluation only).
  • tutor_chat_sessions / tutor_chat_messages — a standalone notes/book AI tutor over
    the demo chapters, driven by a topic-selector + main-tutor agent chain.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Answer-sheet feedback chatbot ────────────────────────────────────────
    op.create_table(
        "subjective_feedback_chats",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("sheet_id", UUID(as_uuid=True), sa.ForeignKey("student_answer_sheets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_subjective_feedback_chats_sheet", "subjective_feedback_chats", ["sheet_id"])

    op.create_table(
        "subjective_feedback_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("chat_id", UUID(as_uuid=True), sa.ForeignKey("subjective_feedback_chats.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),  # student | assistant
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_subjective_feedback_messages_chat", "subjective_feedback_messages", ["chat_id"])

    # ── Standalone AI tutor ──────────────────────────────────────────────────
    op.create_table(
        "tutor_chat_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("student_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_tutor_chat_sessions_student", "tutor_chat_sessions", ["student_id"])

    op.create_table(
        "tutor_chat_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", UUID(as_uuid=True), sa.ForeignKey("tutor_chat_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("answer", sa.Text, nullable=False, server_default=""),
        sa.Column("language", sa.String(30), nullable=True),
        sa.Column("related_mode", sa.String(20), nullable=True),  # objective | subjective | shared
        sa.Column("detected_topic", sa.String(500), nullable=True),
        sa.Column("detected_subtopic_ids", JSONB, nullable=True),
        sa.Column("query_rewrite", sa.Text, nullable=True),
        sa.Column("supporting_knowledge_json", JSONB, nullable=True),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("follow_up_suggestions", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_tutor_chat_messages_session", "tutor_chat_messages", ["session_id"])
    op.create_index("ix_tutor_chat_messages_student", "tutor_chat_messages", ["student_id"])


def downgrade() -> None:
    op.drop_table("tutor_chat_messages")
    op.drop_table("tutor_chat_sessions")
    op.drop_table("subjective_feedback_messages")
    op.drop_table("subjective_feedback_chats")
