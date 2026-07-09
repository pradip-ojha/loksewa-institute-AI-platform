"""Widen chapter/topic/subtopic label columns to VARCHAR(500).

These columns hold syllabus strings copied back from `syllabus_items` after the topic
routers validate them against the exam's live tree. `syllabus_items.{chapter,topic,
subtopic}` are VARCHAR(500), but `subjective_questions` was VARCHAR(120) and the MCQ
tables VARCHAR(255). Real Loksewa syllabi use long bilingual Nepali+English leaf names
(e.g. the ADBL 6.4 topic runs ~260 chars), so writing a validated topic back into the
narrower columns raised `StringDataRightTruncationError` (value too long for type
character varying(120)) and failed the subjective skill-generation job. This aligns all
label columns with the 500-char source of truth so no validated syllabus string can
overflow. Widening a varchar is a metadata-only change (no table rewrite, no data loss).

Revision ID: 023
Revises: 022
Create Date: 2026-07-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (table, old_length) for the columns chapter/topic/subtopic
_WIDEN = [
    ("subjective_questions", 120),
    ("mcq_documents", 255),
    ("mcq_questions", 255),
]
_COLS = ("chapter", "topic", "subtopic")


def upgrade() -> None:
    for table, _old in _WIDEN:
        for col in _COLS:
            op.alter_column(
                table, col,
                type_=sa.String(500),
                existing_nullable=True,
            )


def downgrade() -> None:
    # Truncation could occur on downgrade if long values were stored; guarded by
    # USING substr so it never errors. Restores the prior per-table widths.
    for table, old in _WIDEN:
        for col in _COLS:
            op.alter_column(
                table, col,
                type_=sa.String(old),
                existing_nullable=True,
                postgresql_using=f"substr({col}, 1, {old})",
            )
