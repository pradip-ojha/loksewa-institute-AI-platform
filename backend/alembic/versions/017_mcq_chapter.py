"""MCQ chapter scoping (CLAUDE.md §9, §10).

Exams now hold multiple chapters, so MCQ documents/questions must be tagged with the
admin-chosen chapter (chapter is the PRIMARY syllabus dimension; topic/subtopic are a
finer label within it). `mcq_questions.chapter` already exists (migration 009); this adds
the matching `chapter` to `mcq_documents` so the upload/generation forms can capture it and
propagate it to every produced question. MCQ test-set blueprints distribute by chapter (+
optional topic/subtopic) — that lives inside the existing `topic_distribution` JSONB, so no
column change is needed there.

Revision ID: 017
Revises: 016
Create Date: 2026-06-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("mcq_documents", sa.Column("chapter", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("mcq_documents", "chapter")
