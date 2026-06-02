"""files and processing_jobs tables

Revision ID: 003
Revises: 002
Create Date: 2025-05-30

"""
from typing import Sequence, Union
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS files (
            id                UUID         PRIMARY KEY,
            original_filename VARCHAR(500) NOT NULL,
            display_name      VARCHAR(500) NOT NULL,
            mime_type         VARCHAR(200) NOT NULL,
            file_size         BIGINT       NOT NULL,
            r2_key            VARCHAR(1000) NOT NULL UNIQUE,
            uploaded_by       UUID         NOT NULL REFERENCES users(id),
            created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_files_uploaded_by ON files (uploaded_by)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS processing_jobs (
            id               UUID         PRIMARY KEY,
            job_type         VARCHAR(100) NOT NULL,
            status           VARCHAR(20)  NOT NULL DEFAULT 'queued',
            progress_percent INTEGER      NOT NULL DEFAULT 0,
            current_step     VARCHAR(500),
            input_reference  JSONB,
            output_reference JSONB,
            error_message    TEXT,
            celery_task_id   VARCHAR(255),
            created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            started_at       TIMESTAMPTZ,
            completed_at     TIMESTAMPTZ,
            created_by       UUID         NOT NULL REFERENCES users(id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_jobs_status ON processing_jobs (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_jobs_created_by ON processing_jobs (created_by)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_jobs_created_by")
    op.execute("DROP INDEX IF EXISTS ix_jobs_status")
    op.execute("DROP TABLE IF EXISTS processing_jobs")
    op.execute("DROP INDEX IF EXISTS ix_files_uploaded_by")
    op.execute("DROP TABLE IF EXISTS files")
