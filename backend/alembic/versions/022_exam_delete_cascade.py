"""Make every exam_id FK ON DELETE CASCADE so an exam can be hard-deleted.

Admins can now DELETE an exam (not just archive it). The 10 exam-scoped FKs created by
migration 015 were plain RESTRICT, so deleting an exam with any content raised a
ForeignKeyViolation. Recreate each as ON DELETE CASCADE; each of those tables' own children
already cascade from it (video_*, subjective sheets/evaluations, mcq batches/sets/attempts,
knowledge_chunks, tutor messages, …), so one DELETE on `exams` now wipes all its DB content
atomically. External resources (R2 objects, Pinecone vectors, orphaned `files` rows) are NOT
FK-linked and are cleaned up in `exams.service.delete_exam`.

Revision ID: 022
Revises: 021
Create Date: 2026-07-04
"""
from typing import Sequence, Union

from alembic import op

revision: str = "022"
down_revision: Union[str, None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The 10 tables that carry a NOT NULL exam_id FK (see migration 015 `_EXAM_ID_TABLES`).
_EXAM_ID_TABLES = (
    "syllabus_items",
    "knowledge_documents",
    "knowledge_chunks",
    "mcq_documents",
    "mcq_questions",
    "mcq_test_blueprints",
    "mcq_test_sets",
    "subjective_tests",
    "videos",
    "tutor_chat_sessions",
)


def upgrade() -> None:
    for table in _EXAM_ID_TABLES:
        op.drop_constraint(f"fk_{table}_exam", table, type_="foreignkey")
        op.create_foreign_key(
            f"fk_{table}_exam", table, "exams", ["exam_id"], ["id"], ondelete="CASCADE"
        )


def downgrade() -> None:
    for table in _EXAM_ID_TABLES:
        op.drop_constraint(f"fk_{table}_exam", table, type_="foreignkey")
        op.create_foreign_key(f"fk_{table}_exam", table, "exams", ["exam_id"], ["id"])
