"""ai audit tables

Revision ID: 005
Revises: 004
Create Date: 2026-06-01

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_requests",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("api_version", sa.String(50), nullable=True),
        sa.Column("agent_type", sa.String(100), nullable=True),
        sa.Column("task_type", sa.String(100), nullable=True),
        sa.Column("input_tokens", sa.Integer, nullable=True),
        sa.Column("output_tokens", sa.Integer, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="success"),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("related_entity_type", sa.String(100), nullable=True),
        sa.Column("related_entity_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_ai_requests_agent_type", "ai_requests", ["agent_type"])
    op.create_index("ix_ai_requests_created_at", "ai_requests", ["created_at"])
    op.create_index("ix_ai_requests_related_entity", "ai_requests", ["related_entity_type", "related_entity_id"])

    op.create_table(
        "ai_outputs",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("request_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("ai_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("output_summary", sa.Text, nullable=True),
        sa.Column("quality_notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_ai_outputs_request_id", "ai_outputs", ["request_id"])


def downgrade() -> None:
    op.drop_table("ai_outputs")
    op.drop_table("ai_requests")
