"""Hot-path indexes on chapter (the PRIMARY retrieval dimension) + enrollment reverse lookup.

Chapter became the primary in-exam narrowing key in the multi-exam refactor (017/018) but
was never indexed, while the secondary dimensions (topic/subtopic/complexity/status) were:
  - `mcq_questions.chapter` — filtered FIRST in every test-set-generation bucket
    (`mcq_tests.service._approved_ids`), once per bucket per blueprint run.
  - `knowledge_chunks.(chapter, topic, subtopic)` — SQL-filtered in MCQ-generation knowledge
    retrieval (`mcq_extraction_agent._fetch_knowledge_by_type`), chapter first.
Plus `student_exam_enrollments.exam_id` for the reverse "which students are in exam X" lookup
(only `student_id` was indexed).

Revision ID: 019
Revises: 018
Create Date: 2026-06-30
"""
from typing import Sequence, Union

from alembic import op

revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Index name → (table, column). Built CONCURRENTLY so creating them on a populated
# production table does NOT take a write-blocking lock that would stall inserts /
# answer-checking during the deploy.
_INDEXES = [
    ("ix_mcq_questions_chapter", "mcq_questions", "chapter"),
    ("ix_knowledge_chunks_chapter", "knowledge_chunks", "chapter"),
    ("ix_knowledge_chunks_topic", "knowledge_chunks", "topic"),
    ("ix_knowledge_chunks_subtopic", "knowledge_chunks", "subtopic"),
    ("ix_student_exam_enrollments_exam_id", "student_exam_enrollments", "exam_id"),
]


def upgrade() -> None:
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction, so step outside
    # Alembic's per-migration transaction. IF NOT EXISTS makes a re-run after a
    # partially-failed deploy safe (a failed concurrent build leaves an INVALID index).
    with op.get_context().autocommit_block():
        for name, table, column in _INDEXES:
            op.create_index(name, table, [column], if_not_exists=True, postgresql_concurrently=True)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, table, _column in reversed(_INDEXES):
            op.drop_index(name, table_name=table, if_exists=True, postgresql_concurrently=True)
