"""Persist the knowledge chunks fetched for each question's checking skill.

`fetch_question_resources` retrieves Pinecone chunks per question at skill-generation
time, but the result was discarded after the SkillGenerator consumed it — so the admin
skill-debug endpoint could not show WHAT knowledge each guide was distilled from.
`question_specific_checking_skills.knowledge_context` (nullable JSONB, a list of chunk
dicts: content/topic/subtopic/is_qa/question/chunk_id) stores that snapshot for audit;
it is surfaced only in `build_skill_debug`, never sent to the checker.

Revision ID: 024
Revises: 023
Create Date: 2026-07-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "024"
down_revision: Union[str, None] = "023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "question_specific_checking_skills",
        sa.Column("knowledge_context", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("question_specific_checking_skills", "knowledge_context")
