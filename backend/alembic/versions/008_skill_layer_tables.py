"""Add agent_core_skills and agent_skill_versions tables for the skill layer.

Revision ID: 008
Revises: 007
Create Date: 2026-06-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create agent_skill_versions first (agent_core_skills has an FK pointing here)
    op.create_table(
        "agent_skill_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("skill_id", UUID(as_uuid=True), nullable=False),
        sa.Column("agent_type", sa.String(100), nullable=False),
        sa.Column("scope_type", sa.String(50), nullable=False, server_default="global"),
        sa.Column("scope_id", sa.String(255), nullable=True),
        sa.Column("version_number", sa.Integer, nullable=False, server_default="1"),
        sa.Column("instruction_text", sa.Text, nullable=False),
        sa.Column("structured_rules_json", JSONB, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("change_summary", sa.Text, nullable=True),
    )
    op.create_index("ix_skill_versions_agent_status", "agent_skill_versions", ["agent_type", "status"])
    op.create_index("ix_skill_versions_agent_scope_status", "agent_skill_versions", ["agent_type", "scope_type", "scope_id", "status"])

    op.create_table(
        "agent_core_skills",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_type", sa.String(100), nullable=False, unique=True),
        sa.Column(
            "current_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_skill_versions.id", ondelete="SET NULL", name="fk_core_skill_current_version"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    # Now that agent_core_skills exists, add the FK from agent_skill_versions → agent_core_skills
    op.create_foreign_key(
        "fk_skill_versions_skill_id",
        "agent_skill_versions",
        "agent_core_skills",
        ["skill_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_skill_versions_skill_id", "agent_skill_versions", type_="foreignkey")
    op.drop_table("agent_core_skills")
    op.drop_index("ix_skill_versions_agent_scope_status", "agent_skill_versions")
    op.drop_index("ix_skill_versions_agent_status", "agent_skill_versions")
    op.drop_table("agent_skill_versions")
