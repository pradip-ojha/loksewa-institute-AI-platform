import uuid
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.knowledge.models import KnowledgeDocument, KnowledgeChunk


async def list_documents(db: AsyncSession, skip: int = 0, limit: int = 50) -> list[KnowledgeDocument]:
    result = await db.execute(
        select(KnowledgeDocument).order_by(KnowledgeDocument.created_at.desc()).offset(skip).limit(limit)
    )
    return list(result.scalars().all())


async def get_document(db: AsyncSession, document_id: uuid.UUID) -> KnowledgeDocument | None:
    result = await db.execute(select(KnowledgeDocument).where(KnowledgeDocument.id == document_id))
    return result.scalar_one_or_none()


async def list_chunks(db: AsyncSession, document_id: uuid.UUID) -> list[KnowledgeChunk]:
    result = await db.execute(
        select(KnowledgeChunk)
        .where(KnowledgeChunk.document_id == document_id)
        .order_by(KnowledgeChunk.chunk_index)
    )
    return list(result.scalars().all())


async def delete_document(db: AsyncSession, doc: KnowledgeDocument) -> list[str]:
    """Delete document + chunks from DB and return Pinecone vector IDs to remove."""
    chunk_result = await db.execute(
        select(KnowledgeChunk.pinecone_vector_id).where(KnowledgeChunk.document_id == doc.id)
    )
    vector_ids = [vid for (vid,) in chunk_result.all() if vid]

    await db.execute(
        KnowledgeChunk.__table__.delete().where(KnowledgeChunk.document_id == doc.id)
    )
    await db.delete(doc)
    await db.commit()
    return vector_ids
