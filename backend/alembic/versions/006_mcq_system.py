"""mcq system tables

Revision ID: 006
Revises: 005
Create Date: 2026-06-01

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mcq_documents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("origin_type", sa.String(50), nullable=False),   # 'uploaded_document' | 'generation_source'
        sa.Column("file_id", UUID(as_uuid=True), sa.ForeignKey("files.id", ondelete="SET NULL"), nullable=True),
        sa.Column("topic", sa.String(255), nullable=True),
        sa.Column("subtopic", sa.String(255), nullable=True),
        sa.Column("custom_instruction", sa.Text, nullable=True),
        sa.Column("processing_status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("question_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_mcq_documents_created_by", "mcq_documents", ["created_by"])
    op.create_index("ix_mcq_documents_status", "mcq_documents", ["processing_status"])

    op.create_table(
        "mcq_review_batches",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("document_id", UUID(as_uuid=True), sa.ForeignKey("mcq_documents.id", ondelete="CASCADE"), nullable=True),
        sa.Column("batch_type", sa.String(50), nullable=False),    # 'extraction' | 'generation' | 'regeneration'
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("total_questions", sa.Integer, nullable=False, server_default="0"),
        sa.Column("accepted_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("rejected_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("rejection_feedback", sa.Text, nullable=True),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("processing_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_mcq_review_batches_document_id", "mcq_review_batches", ["document_id"])
    op.create_index("ix_mcq_review_batches_status", "mcq_review_batches", ["status"])

    op.create_table(
        "mcq_questions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source_document_id", UUID(as_uuid=True), sa.ForeignKey("mcq_documents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("review_batch_id", UUID(as_uuid=True), sa.ForeignKey("mcq_review_batches.id", ondelete="SET NULL"), nullable=True),
        sa.Column("origin_type", sa.String(50), nullable=False),   # 'uploaded_extracted' | 'ai_generated' | 'manual'
        sa.Column("question_text", sa.Text, nullable=False),
        sa.Column("options", JSONB, nullable=False),               # [{"id":"A","label":"A","text":"..."}]
        sa.Column("correct_option_ids", JSONB, nullable=False),    # ["A"]
        sa.Column("explanation", sa.Text, nullable=True),
        sa.Column("chapter", sa.String(255), nullable=True),
        sa.Column("topic", sa.String(255), nullable=True),
        sa.Column("subtopic", sa.String(255), nullable=True),
        sa.Column("complexity", sa.String(20), nullable=False, server_default="medium"),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("review_feedback", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), onupdate=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_mcq_questions_status", "mcq_questions", ["status"])
    op.create_index("ix_mcq_questions_topic", "mcq_questions", ["topic"])
    op.create_index("ix_mcq_questions_subtopic", "mcq_questions", ["subtopic"])
    op.create_index("ix_mcq_questions_complexity", "mcq_questions", ["complexity"])
    op.create_index("ix_mcq_questions_review_batch_id", "mcq_questions", ["review_batch_id"])

    op.create_table(
        "mcq_rejection_feedback",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("batch_id", UUID(as_uuid=True), sa.ForeignKey("mcq_review_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("feedback_text", sa.Text, nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("mcq_rejection_feedback")
    op.drop_table("mcq_questions")
    op.drop_table("mcq_review_batches")
    op.drop_table("mcq_documents")
