"""Add answer_format to mcq_documents.

Revision ID: 025
Revises: 024
Create Date: 2026-07-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "025"
down_revision: Union[str, None] = "024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "mcq_documents",
        sa.Column(
            "answer_format",
            sa.String(20),
            nullable=False,
            server_default="inline",
        ),
    )


def downgrade() -> None:
    op.drop_column("mcq_documents", "answer_format")
