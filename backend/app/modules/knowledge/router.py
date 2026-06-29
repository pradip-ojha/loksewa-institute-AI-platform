import uuid

from fastapi import APIRouter, Depends, Form, UploadFile, File as FastAPIFile, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_admin
from app.core.database import get_db
from app.core.exceptions import AppException
from app.integrations.pinecone_client import get_pinecone
from app.modules.exams.service import get_exam_or_404
from app.modules.files.service import store_upload
from app.modules.jobs.service import create_job, update_job
from app.modules.jobs.models import JobStatus
from app.modules.jobs.schemas import JobOut
from app.modules.knowledge.models import KnowledgeDocument
from app.modules.knowledge.schemas import (
    KnowledgeChunkOut,
    KnowledgeDocumentOut,
    KnowledgeDocumentWithJob,
)
from app.modules.knowledge.service import (
    delete_document,
    get_document,
    list_chunks,
    list_documents,
)
from app.modules.users.models import User

router = APIRouter(prefix="/admin/knowledge", tags=["knowledge"])

VALID_DOC_TYPES = {"notes", "book_content", "handout", "reference_material"}


@router.post("/documents", response_model=KnowledgeDocumentWithJob, status_code=201)
async def upload_knowledge_document(
    display_name: str = Form(...),
    document_type: str = Form(...),
    exam_id: uuid.UUID = Form(...),
    chapter: str | None = Form(None),
    topic: str | None = Form(None),
    subtopic: str | None = Form(None),
    custom_instruction: str | None = Form(None),
    file: UploadFile = FastAPIFile(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> KnowledgeDocumentWithJob:
    if document_type not in VALID_DOC_TYPES:
        raise AppException(422, "invalid_document_type", f"document_type must be one of: {', '.join(VALID_DOC_TYPES)}")
    await get_exam_or_404(db, exam_id)

    file_record = await store_upload(
        file,
        context="document",
        display_name=display_name,
        uploaded_by=current_user.id,
        db=db,
    )

    doc = KnowledgeDocument(
        display_name=display_name,
        document_type=document_type,
        exam_id=exam_id,
        chapter=(chapter or None),
        file_id=file_record.id,
        topic=topic or None,
        subtopic=subtopic or None,
        custom_instruction=custom_instruction or None,
        processing_status="pending",
        created_by=current_user.id,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    job = await create_job(
        db,
        job_type="knowledge_processing",
        created_by=current_user.id,
        input_reference={"document_id": str(doc.id)},
    )

    from app.core.celery_client import get_celery
    task = get_celery().send_task(
        "workers.tasks.knowledge_tasks.process_knowledge_document",
        args=[str(job.id), str(doc.id)],
        queue="kvi_ai_knowledge",
    )
    await update_job(db, job.id, celery_task_id=task.id, status=JobStatus.queued)

    return KnowledgeDocumentWithJob(
        **KnowledgeDocumentOut.model_validate(doc).model_dump(),
        job_id=job.id,
    )


@router.get("/documents", response_model=list[KnowledgeDocumentOut])
async def get_documents(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> list[KnowledgeDocumentOut]:
    docs = await list_documents(db, skip=skip, limit=limit)
    return [KnowledgeDocumentOut.model_validate(d) for d in docs]


@router.get("/documents/{document_id}/chunks", response_model=list[KnowledgeChunkOut])
async def get_document_chunks(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> list[KnowledgeChunkOut]:
    doc = await get_document(db, document_id)
    if not doc:
        raise AppException(404, "not_found", "Document not found.")
    chunks = await list_chunks(db, document_id)
    return [KnowledgeChunkOut.model_validate(c) for c in chunks]


@router.delete("/documents/{document_id}", status_code=204)
async def delete_knowledge_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> None:
    doc = await get_document(db, document_id)
    if not doc:
        raise AppException(404, "not_found", "Document not found.")

    vector_ids = await delete_document(db, doc)

    if vector_ids:
        import asyncio
        await asyncio.to_thread(get_pinecone().delete_vectors, vector_ids)


@router.get("/documents/{document_id}/reprocess", response_model=JobOut, status_code=202)
async def reprocess_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> JobOut:
    doc = await get_document(db, document_id)
    if not doc:
        raise AppException(404, "not_found", "Document not found.")

    # Remove existing chunks and vectors before reprocessing
    from app.modules.knowledge.service import list_chunks, delete_document
    from app.modules.knowledge.models import KnowledgeChunk
    from sqlalchemy import select
    chunk_result = await db.execute(
        select(KnowledgeChunk.pinecone_vector_id).where(KnowledgeChunk.document_id == doc.id)
    )
    old_vector_ids = [vid for (vid,) in chunk_result.all() if vid]
    if old_vector_ids:
        import asyncio
        await asyncio.to_thread(get_pinecone().delete_vectors, old_vector_ids)

    await db.execute(KnowledgeChunk.__table__.delete().where(KnowledgeChunk.document_id == doc.id))
    doc.chunk_count = 0
    doc.processing_status = "pending"
    await db.commit()

    job = await create_job(
        db,
        job_type="knowledge_processing",
        created_by=current_user.id,
        input_reference={"document_id": str(doc.id)},
    )
    from app.core.celery_client import get_celery
    task = get_celery().send_task(
        "workers.tasks.knowledge_tasks.process_knowledge_document",
        args=[str(job.id), str(doc.id)],
        queue="kvi_ai_knowledge",
    )
    await update_job(db, job.id, celery_task_id=task.id, status=JobStatus.queued)

    await db.refresh(job)
    return JobOut.model_validate(job)
