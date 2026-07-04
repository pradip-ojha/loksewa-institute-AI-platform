"""Exam + enrollment service. The `exam_id` → `exam_type` resolution helpers here are
reused across knowledge/mcq/subjective/video/tutor so the routing key stays consistent."""
import logging
import uuid

from sqlalchemy import select, delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.modules.exams.models import EXAM_TYPES, Exam, StudentExamEnrollment

logger = logging.getLogger(__name__)


async def get_exam_or_404(db: AsyncSession, exam_id: uuid.UUID) -> Exam:
    exam = (await db.execute(select(Exam).where(Exam.id == exam_id))).scalar_one_or_none()
    if not exam:
        raise AppException(404, "exam_not_found", "Exam not found.")
    return exam


async def get_exam_type(db: AsyncSession, exam_id: uuid.UUID) -> str:
    """Resolve an exam's type (objective|subjective) — the value derived everywhere
    that used to read `content_usage_type`."""
    return (await get_exam_or_404(db, exam_id)).exam_type


async def create_exam(db: AsyncSession, *, exam_type: str, name: str, description: str | None, created_by: uuid.UUID) -> Exam:
    if exam_type not in EXAM_TYPES:
        raise AppException(422, "invalid_exam_type", "exam_type must be 'objective' or 'subjective'.")
    name = (name or "").strip()
    if not name:
        raise AppException(422, "invalid_name", "Exam name is required.")
    exam = Exam(id=uuid.uuid4(), exam_type=exam_type, name=name, description=(description or None), created_by=created_by)
    db.add(exam)
    await db.commit()
    await db.refresh(exam)
    return exam


async def list_exams(db: AsyncSession, *, exam_type: str | None = None, include_archived: bool = True) -> list[Exam]:
    stmt = select(Exam).order_by(Exam.created_at.desc())
    if exam_type:
        stmt = stmt.where(Exam.exam_type == exam_type)
    if not include_archived:
        stmt = stmt.where(Exam.status == "active")
    return list((await db.execute(stmt)).scalars().all())


async def update_exam(db: AsyncSession, exam_id: uuid.UUID, *, name: str | None, description: str | None, status: str | None) -> Exam:
    exam = await get_exam_or_404(db, exam_id)
    if name is not None:
        n = name.strip()
        if not n:
            raise AppException(422, "invalid_name", "Exam name cannot be empty.")
        exam.name = n
    if description is not None:
        exam.description = description or None
    if status is not None:
        if status not in ("active", "archived"):
            raise AppException(422, "invalid_status", "status must be 'active' or 'archived'.")
        exam.status = status
    await db.commit()
    await db.refresh(exam)
    return exam


# SQL that collects every R2-backed file (id + key) belonging to an exam: the exam-scoped
# resource tables plus the child tables whose files are reached through their parent. Files
# are NOT exam-scoped and their FKs are SET NULL, so a DB cascade would orphan both the `files`
# rows and the R2 objects — this is what lets `delete_exam` clean them up explicitly.
_EXAM_FILE_IDS_SQL = text("""
    SELECT f.id AS id, f.r2_key AS r2_key FROM files f WHERE f.id IN (
        SELECT file_id FROM knowledge_documents WHERE exam_id = :eid
        UNION SELECT file_id FROM mcq_documents WHERE exam_id = :eid
        UNION SELECT question_paper_file_id FROM subjective_tests WHERE exam_id = :eid
        UNION SELECT model_answer_file_id FROM subjective_tests WHERE exam_id = :eid
        UNION SELECT sample_marked_file_id FROM subjective_tests WHERE exam_id = :eid
        UNION SELECT rubric_file_id FROM subjective_tests WHERE exam_id = :eid
        UNION SELECT s.file_id FROM student_answer_sheets s
               JOIN subjective_tests t ON s.test_id = t.id WHERE t.exam_id = :eid
        UNION SELECT pa.checked_file_id FROM pdf_annotations pa
               JOIN student_answer_sheets s ON pa.sheet_id = s.id
               JOIN subjective_tests t ON s.test_id = t.id WHERE t.exam_id = :eid
        UNION SELECT file_id FROM videos WHERE exam_id = :eid
        UNION SELECT audio_file_id FROM videos WHERE exam_id = :eid
        UNION SELECT support_slides_file_id FROM videos WHERE exam_id = :eid
        UNION SELECT vs.file_id FROM video_support_slides vs
               JOIN videos v ON vs.video_id = v.id WHERE v.exam_id = :eid
    )
""")


async def delete_exam(db: AsyncSession, exam_id: uuid.UUID) -> None:
    """Hard-delete an exam and ALL of its content.

    The 10 exam_id FKs are ON DELETE CASCADE (migration 022), and each of those tables' own
    children cascade from it, so deleting the `exams` row wipes every exam-scoped DB row
    atomically (syllabus, knowledge, MCQ, MCQ tests + attempts, subjective tests + sheets +
    evaluations, videos + transcripts, tutor chats, enrollments). This also removes the
    external resources a DB cascade cannot: the exam's Pinecone vectors, its R2 objects, and
    the now-orphaned `files` rows. External cleanup is best-effort — a storage hiccup never
    blocks (or rolls back) the DB delete.
    """
    exam = await get_exam_or_404(db, exam_id)

    # 1. Gather what the DB cascade cannot clean up, BEFORE the rows disappear.
    vector_ids = [
        vid for (vid,) in (await db.execute(text(
            "SELECT pinecone_vector_id FROM knowledge_chunks "
            "WHERE exam_id = :eid AND pinecone_vector_id IS NOT NULL"
        ), {"eid": exam_id})).all()
    ]
    file_rows = (await db.execute(_EXAM_FILE_IDS_SQL, {"eid": exam_id})).mappings().all()
    file_ids = [r["id"] for r in file_rows]
    r2_keys = [r["r2_key"] for r in file_rows if r["r2_key"]]

    # 2. Delete the exam (DB cascade removes all exam-scoped content), then the orphaned
    #    files rows (not exam-scoped, so not cascaded). One transaction.
    await db.delete(exam)
    if file_ids:
        await db.execute(text("DELETE FROM files WHERE id = ANY(:ids)"), {"ids": file_ids})
    await db.commit()

    # 3. Best-effort external cleanup AFTER the DB delete is committed.
    if vector_ids:
        try:
            import asyncio
            from app.integrations.pinecone_client import get_pinecone
            pinecone = get_pinecone()
            # delete_vectors issues one API call; batch to stay under provider per-call limits.
            for start in range(0, len(vector_ids), 1000):
                await asyncio.to_thread(pinecone.delete_vectors, vector_ids[start:start + 1000])
        except Exception as exc:
            logger.warning("Exam %s deleted, but Pinecone cleanup failed for %d vectors: %s",
                           exam_id, len(vector_ids), exc)

    if r2_keys:
        try:
            import asyncio
            from app.integrations.r2_client import get_r2
            await asyncio.to_thread(get_r2().delete_objects, r2_keys)
        except Exception as exc:
            logger.warning("Exam %s deleted, but R2 cleanup failed for %d objects: %s",
                           exam_id, len(r2_keys), exc)


# ── Enrollment ──────────────────────────────────────────────────────────────

async def enroll_student(db: AsyncSession, *, student_id: uuid.UUID, exam_id: uuid.UUID) -> None:
    await get_exam_or_404(db, exam_id)
    exists = (await db.execute(
        select(StudentExamEnrollment).where(
            StudentExamEnrollment.student_id == student_id,
            StudentExamEnrollment.exam_id == exam_id,
        )
    )).scalar_one_or_none()
    if exists:
        return
    db.add(StudentExamEnrollment(id=uuid.uuid4(), student_id=student_id, exam_id=exam_id))
    await db.commit()


async def unenroll_student(db: AsyncSession, *, student_id: uuid.UUID, exam_id: uuid.UUID) -> None:
    await db.execute(
        delete(StudentExamEnrollment).where(
            StudentExamEnrollment.student_id == student_id,
            StudentExamEnrollment.exam_id == exam_id,
        )
    )
    await db.commit()


async def get_enrolled_exam_ids(db: AsyncSession, student_id: uuid.UUID) -> list[uuid.UUID]:
    rows = (await db.execute(
        select(StudentExamEnrollment.exam_id).where(StudentExamEnrollment.student_id == student_id)
    )).scalars().all()
    return list(rows)


async def list_student_enrollments(db: AsyncSession, student_id: uuid.UUID) -> list[dict]:
    rows = (await db.execute(
        select(Exam, StudentExamEnrollment.enrolled_at)
        .join(StudentExamEnrollment, StudentExamEnrollment.exam_id == Exam.id)
        .where(StudentExamEnrollment.student_id == student_id)
        .order_by(StudentExamEnrollment.enrolled_at.desc())
    )).all()
    return [
        {"exam_id": exam.id, "exam_type": exam.exam_type, "name": exam.name, "enrolled_at": enrolled_at}
        for exam, enrolled_at in rows
    ]


async def ensure_enrolled(db: AsyncSession, *, student_id: uuid.UUID, exam_id: uuid.UUID) -> None:
    """Guard for student endpoints: 403 if the student is not enrolled in the exam."""
    ids = await get_enrolled_exam_ids(db, student_id)
    if exam_id not in ids:
        raise AppException(403, "not_enrolled", "You are not enrolled in this exam.")
