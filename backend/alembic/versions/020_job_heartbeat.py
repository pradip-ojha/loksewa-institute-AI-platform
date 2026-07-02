"""Worker liveness columns on processing_jobs for multi-worker-safe job recovery.

Adds `owner_token` (which worker process is running the job) and `last_heartbeat_at`
(refreshed periodically by that worker). Startup recovery and the reaper now fail a
`processing` job only when its heartbeat has gone stale, so restarting/scaling one
worker can never fail another live worker's in-flight job. See workers/runtime.py
(`_heartbeat_loop`) and app/modules/jobs/service.py.

Revision ID: 020
Revises: 019
Create Date: 2026-06-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "processing_jobs",
        sa.Column("owner_token", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "processing_jobs",
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("processing_jobs", "last_heartbeat_at")
    op.drop_column("processing_jobs", "owner_token")
