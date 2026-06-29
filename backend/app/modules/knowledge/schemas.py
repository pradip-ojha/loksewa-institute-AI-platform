import uuid
from datetime import datetime
from pydantic import BaseModel


class KnowledgeDocumentCreate(BaseModel):
    display_name: str
    document_type: str
    exam_id: uuid.UUID
    chapter: str | None = None
    topic: str | None = None
    subtopic: str | None = None
    custom_instruction: str | None = None


class KnowledgeChunkOut(BaseModel):
    id: uuid.UUID
    chunk_index: int
    content: str
    content_type: str | None
    chapter: str | None
    topic: str | None
    subtopic: str | None
    language: str | None
    pinecone_vector_id: str | None
    quality_status: str | None
    chunk_metadata: dict | None = None

    model_config = {"from_attributes": True}


class KnowledgeDocumentOut(BaseModel):
    id: uuid.UUID
    display_name: str
    document_type: str
    exam_id: uuid.UUID
    chapter: str | None
    topic: str | None
    subtopic: str | None
    processing_status: str
    chunk_count: int
    file_id: uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}


class KnowledgeDocumentWithJob(KnowledgeDocumentOut):
    job_id: uuid.UUID
