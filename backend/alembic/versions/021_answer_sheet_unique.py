"""Unique (test_id, student_id, upload_attempt_number) on student_answer_sheets.

Makes the read-modify-write in `upload_answer` race-safe: two concurrent uploads for the
same (test, student) compute the same next attempt number, but only one INSERT can land —
the loser hits IntegrityError and is handled as a duplicate submission instead of creating
a second sheet + second checking job (double AI spend). See app/modules/subjective/
{models,router}.py.

Revision ID: 021
Revises: 020
Create Date: 2026-06-30
"""
from typing import Sequence, Union

from alembic import op

revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_answer_sheet_test_student_attempt",
        "student_answer_sheets",
        ["test_id", "student_id", "upload_attempt_number"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_answer_sheet_test_student_attempt",
        "student_answer_sheets",
        type_="unique",
    )
