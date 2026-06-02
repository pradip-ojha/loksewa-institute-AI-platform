"""initial users table

Revision ID: 001
Revises:
Create Date: 2025-05-30

"""
from typing import Sequence, Union

from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use raw SQL throughout to avoid SQLAlchemy Enum DDL events firing
    # (create_type=False is not reliable across all SA 2.x versions)

    op.execute("""
        DO $$ BEGIN
            CREATE TYPE user_role AS ENUM ('institute_admin', 'student');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE user_status AS ENUM ('active', 'inactive');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            UUID         PRIMARY KEY,
            full_name     VARCHAR(255) NOT NULL,
            email         VARCHAR(255) NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            phone         VARCHAR(50),
            role          user_role    NOT NULL,
            status        user_status  NOT NULL DEFAULT 'active',
            created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            last_login_at TIMESTAMPTZ
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_users_email")
    op.execute("DROP TABLE IF EXISTS users")
    op.execute("DROP TYPE IF EXISTS user_role")
    op.execute("DROP TYPE IF EXISTS user_status")
