"""Enforce content_usage_type IN ('objective','subjective') at DB level.

Removes implicit 'shared' allowance and adds a CHECK constraint so the column
can never hold an invalid value going forward.

Revision ID: 007
Revises: 006
Create Date: 2026-06-02
"""
from typing import Sequence, Union
from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Convert any legacy 'shared' rows before adding the constraint so the
    # migration is safe to run against existing data.
    op.execute("""
        UPDATE knowledge_documents
        SET content_usage_type = 'objective'
        WHERE content_usage_type = 'shared'
    """)
    op.execute("""
        ALTER TABLE knowledge_documents
        ADD CONSTRAINT ck_knowledge_documents_content_usage_type
        CHECK (content_usage_type IN ('objective', 'subjective'))
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE knowledge_documents
        DROP CONSTRAINT IF EXISTS ck_knowledge_documents_content_usage_type
    """)
