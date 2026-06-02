"""syllabus_items table

Revision ID: 002
Revises: 001
Create Date: 2025-05-30

"""
from typing import Sequence, Union
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE syllabus_type AS ENUM ('objective', 'subjective');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS syllabus_items (
            id            UUID        PRIMARY KEY,
            syllabus_type syllabus_type NOT NULL,
            chapter       VARCHAR(500) NOT NULL,
            topic         VARCHAR(500) NOT NULL,
            subtopic      VARCHAR(500),
            sort_order    INTEGER      NOT NULL DEFAULT 0,
            is_active     BOOLEAN      NOT NULL DEFAULT TRUE
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_syllabus_items_type ON syllabus_items (syllabus_type)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_syllabus_items_type")
    op.execute("DROP TABLE IF EXISTS syllabus_items")
    op.execute("DROP TYPE IF EXISTS syllabus_type")
