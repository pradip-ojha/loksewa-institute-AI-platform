"""Chapter threading + model-answer handwritten flag (CLAUDE.md §8, §11, §13).

Chapter is the PRIMARY retrieval dimension. This migration lets the subjective, video and
tutor retrieval paths filter knowledge by chapter (they previously dropped it and could pull
chunks from the wrong chapter within an exam):
  - `subjective_questions.chapter` — resolved from the routed topic; used to chapter-scope the
    Pinecone filter in `fetch_question_resources`.
  - `videos.chapter` — a lecture is uploaded under one chapter (like an MCQ document); video Q&A
    knowledge retrieval filters Pinecone by it.
  - `video_timeline_segments.chapter` — inherited from the parent video.
Plus `subjective_tests.model_answer_is_handwritten` — routes an image/scanned model answer to
Gemini (handwriting) vs Azure gpt-5 typed vision (CLAUDE.md §4 governing principle).

Revision ID: 018
Revises: 017
Create Date: 2026-06-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("subjective_questions", sa.Column("chapter", sa.String(120), nullable=True))
    op.add_column(
        "subjective_tests",
        sa.Column("model_answer_is_handwritten", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("videos", sa.Column("chapter", sa.String(500), nullable=True))
    op.add_column("video_timeline_segments", sa.Column("chapter", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("video_timeline_segments", "chapter")
    op.drop_column("videos", "chapter")
    op.drop_column("subjective_tests", "model_answer_is_handwritten")
    op.drop_column("subjective_questions", "chapter")
