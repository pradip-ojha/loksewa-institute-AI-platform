"""skill builder chat tables (skill_update_chats, skill_update_messages)

Revision ID: 012
Revises: 011
Create Date: 2026-06-06

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "skill_update_chats",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_type", sa.String(100), nullable=False),
        sa.Column("scope_type", sa.String(50), nullable=False, server_default="global"),
        sa.Column("scope_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("draft_version_id", UUID(as_uuid=True), sa.ForeignKey("agent_skill_versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_skill_update_chats_agent_status", "skill_update_chats", ["agent_type", "status"])

    op.create_table(
        "skill_update_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("chat_id", UUID(as_uuid=True), sa.ForeignKey("skill_update_chats.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_skill_update_messages_chat_id", "skill_update_messages", ["chat_id"])


def downgrade() -> None:
    op.drop_table("skill_update_messages")
    op.drop_table("skill_update_chats")
