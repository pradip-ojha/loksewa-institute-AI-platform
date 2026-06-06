"""subjective checking v2: per-question topic/subtopic, skill evaluation audit, locator plan

Revision ID: 013
Revises: 012
Create Date: 2026-06-06

Adds the columns the examiner-skill–driven checking pipeline needs:
  • subjective_questions.topic / .subtopic — detected per question (used to fetch
    supporting knowledge during skill generation).
  • question_specific_checking_skills.evaluation_status / .evaluation_notes /
    .iterations — audit of the Skill Evaluator pass.
  • pdf_annotations.locator_plan — the vision locator + geometry-validation result
    per annotation target, persisted for audit.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("subjective_questions", sa.Column("topic", sa.String(120), nullable=True))
    op.add_column("subjective_questions", sa.Column("subtopic", sa.String(120), nullable=True))

    op.add_column("question_specific_checking_skills", sa.Column("evaluation_status", sa.String(30), nullable=True))
    op.add_column("question_specific_checking_skills", sa.Column("evaluation_notes", sa.Text, nullable=True))
    op.add_column(
        "question_specific_checking_skills",
        sa.Column("iterations", sa.Integer, nullable=False, server_default="1"),
    )

    op.add_column("pdf_annotations", sa.Column("locator_plan", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("pdf_annotations", "locator_plan")
    op.drop_column("question_specific_checking_skills", "iterations")
    op.drop_column("question_specific_checking_skills", "evaluation_notes")
    op.drop_column("question_specific_checking_skills", "evaluation_status")
    op.drop_column("subjective_questions", "subtopic")
    op.drop_column("subjective_questions", "topic")
