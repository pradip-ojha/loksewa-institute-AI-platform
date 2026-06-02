"""knowledge layer tables

Revision ID: 004
Revises: 003
Create Date: 2025-05-30

"""
from typing import Sequence, Union
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_documents (
            id                  UUID         PRIMARY KEY,
            display_name        VARCHAR(500) NOT NULL,
            document_type       VARCHAR(50)  NOT NULL,
            content_usage_type  VARCHAR(50)  NOT NULL,
            file_id             UUID         NOT NULL REFERENCES files(id),
            topic               VARCHAR(500),
            subtopic            VARCHAR(500),
            custom_instruction  TEXT,
            processing_status   VARCHAR(50)  NOT NULL DEFAULT 'pending',
            chunk_count         INTEGER      NOT NULL DEFAULT 0,
            created_by          UUID         NOT NULL REFERENCES users(id),
            created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_kdoc_created_by ON knowledge_documents (created_by)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_kdoc_status ON knowledge_documents (processing_status)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_chunks (
            id                  UUID         PRIMARY KEY,
            document_id         UUID         NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
            chunk_index         INTEGER      NOT NULL,
            content             TEXT         NOT NULL,
            content_type        VARCHAR(100),
            chapter             VARCHAR(500),
            topic               VARCHAR(500),
            subtopic            VARCHAR(500),
            language            VARCHAR(100),
            pinecone_vector_id  VARCHAR(255),
            quality_status      VARCHAR(50),
            metadata            JSONB
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_kchunk_document_id ON knowledge_chunks (document_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_kchunk_pinecone_id ON knowledge_chunks (pinecone_vector_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_kchunk_pinecone_id")
    op.execute("DROP INDEX IF EXISTS ix_kchunk_document_id")
    op.execute("DROP TABLE IF EXISTS knowledge_chunks")
    op.execute("DROP INDEX IF EXISTS ix_kdoc_status")
    op.execute("DROP INDEX IF EXISTS ix_kdoc_created_by")
    op.execute("DROP TABLE IF EXISTS knowledge_documents")
