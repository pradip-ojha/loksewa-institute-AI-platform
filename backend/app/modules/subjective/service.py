"""Subjective test + answer-sheet checking service.

Holds DB queries, signed-URL helpers, question-wise reconstruction, and
result-building. The heavy AI pipeline lives in the Celery task
(`workers/tasks/subjective_tasks.py`) which calls the agents in
`app/ai/agents/`; this module is the shared, AI-free logic both the router and
the task rely on.

Source-of-truth rule: per-question `marks` on SubjectiveQuestion is the hard cap
for awardable marks — enforced in `clamp_marks` regardless of what the AI returns.
"""
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.r2_client import get_r2
from app.modules.files.models import File
from app.modules.subjective.models import (
    AnswerEvaluation, AnswerQualityCheck, PDFAnnotation,
    StudentAnswerSheet, SubjectiveQuestion, SubjectiveTest,
)
from app.modules.users.models import User

MAX_UPLOAD_ATTEMPTS = 2


# ── Signed URLs ─────────────────────────────────────────────────────────────────

async def signed_url(db: AsyncSession, file_id: uuid.UUID | None) -> str | None:
    if not file_id:
        return None
    r = await db.execute(select(File).where(File.id == file_id))
    f = r.scalar_one_or_none()
    if not f:
        return None
    try:
        return get_r2().get_signed_url(f.r2_key)
    except Exception:
        return None


# ── Admin: tests ────────────────────────────────────────────────────────────────

async def get_test(db: AsyncSession, test_id: uuid.UUID) -> SubjectiveTest | None:
    r = await db.execute(select(SubjectiveTest).where(SubjectiveTest.id == test_id))
    return r.scalar_one_or_none()


async def list_tests(db: AsyncSession, *, page: int = 1, per_page: int = 20):
    total = (await db.execute(select(func.count()).select_from(SubjectiveTest))).scalar_one()
    r = await db.execute(
        select(SubjectiveTest).order_by(SubjectiveTest.created_at.desc())
        .offset((page - 1) * per_page).limit(per_page)
    )
    return r.scalars().all(), total


async def get_test_questions(db: AsyncSession, test_id: uuid.UUID) -> list[SubjectiveQuestion]:
    r = await db.execute(
        select(SubjectiveQuestion)
        .where(SubjectiveQuestion.test_id == test_id)
        .order_by(SubjectiveQuestion.question_order)
    )
    return list(r.scalars().all())


async def set_status(db: AsyncSession, test_id: uuid.UUID, status: str) -> SubjectiveTest | None:
    t = await get_test(db, test_id)
    if not t:
        return None
    t.status = status
    await db.commit()
    await db.refresh(t)
    return t


async def delete_test(db: AsyncSession, test_id: uuid.UUID) -> bool:
    t = await get_test(db, test_id)
    if not t:
        return False
    await db.delete(t)
    await db.commit()
    return True


def test_out_fields(t: SubjectiveTest) -> dict:
    return {
        "id": t.id,
        "display_name": t.display_name,
        "total_time_minutes": t.total_time_minutes,
        "num_questions": t.num_questions,
        "total_marks": t.total_marks,
        "status": t.status,
        "skill_generation_status": t.skill_generation_status,
        "skill_generation_job_id": t.skill_generation_job_id,
        "has_rubric": t.rubric_file_id is not None,
        "created_at": t.created_at,
    }


# ── Admin: submissions ──────────────────────────────────────────────────────────

async def list_submissions(db: AsyncSession, test_id: uuid.UUID) -> list[dict]:
    r = await db.execute(
        select(StudentAnswerSheet, User)
        .join(User, User.id == StudentAnswerSheet.student_id)
        .where(StudentAnswerSheet.test_id == test_id)
        .order_by(StudentAnswerSheet.created_at.desc())
    )
    out: list[dict] = []
    for sheet, user in r.all():
        evaluation = await _latest_evaluation(db, sheet.id)
        annotation = await _latest_annotation(db, sheet.id)
        checked_url = await signed_url(db, annotation.checked_file_id) if annotation else None
        out.append({
            "sheet_id": sheet.id,
            "student_id": user.id,
            "student_name": user.full_name,
            "student_email": user.email,
            "upload_attempt_number": sheet.upload_attempt_number,
            "current_status": sheet.current_status,
            "total_marks_awarded": evaluation.total_marks_awarded if evaluation else None,
            "total_marks_possible": evaluation.total_marks_possible if evaluation else None,
            "checked_pdf_url": checked_url,
            "created_at": sheet.created_at,
        })
    return out


# ── Student ─────────────────────────────────────────────────────────────────────

async def list_student_tests(db: AsyncSession, student_id: uuid.UUID) -> list[dict]:
    """Active tests + any test this student already submitted to, each with the
    student's own latest submission status."""
    sheets = await _student_sheets_by_test(db, student_id)
    test_ids = set(sheets.keys())

    cond = SubjectiveTest.status == "active"
    if test_ids:
        cond = cond | SubjectiveTest.id.in_(test_ids)
    r = await db.execute(select(SubjectiveTest).where(cond).order_by(SubjectiveTest.created_at.desc()))

    out: list[dict] = []
    for t in r.scalars().all():
        sheet = sheets.get(t.id)
        evaluation = await _latest_evaluation(db, sheet.id) if sheet else None
        out.append({
            "test_id": t.id,
            "display_name": t.display_name,
            "total_time_minutes": t.total_time_minutes,
            "num_questions": t.num_questions,
            "total_marks": t.total_marks,
            "submission_status": _submission_status(sheet),
            "sheet_id": sheet.id if sheet else None,
            "upload_attempt_number": sheet.upload_attempt_number if sheet else 0,
            "total_marks_awarded": evaluation.total_marks_awarded if evaluation else None,
        })
    return out


async def get_latest_sheet(db: AsyncSession, test_id: uuid.UUID, student_id: uuid.UUID) -> StudentAnswerSheet | None:
    r = await db.execute(
        select(StudentAnswerSheet)
        .where(StudentAnswerSheet.test_id == test_id, StudentAnswerSheet.student_id == student_id)
        .order_by(StudentAnswerSheet.upload_attempt_number.desc())
        .limit(1)
    )
    return r.scalar_one_or_none()


async def get_sheet(db: AsyncSession, sheet_id: uuid.UUID) -> StudentAnswerSheet | None:
    r = await db.execute(select(StudentAnswerSheet).where(StudentAnswerSheet.id == sheet_id))
    return r.scalar_one_or_none()


async def build_student_result(db: AsyncSession, sheet: StudentAnswerSheet) -> dict:
    """Assemble the student-facing result (no internal JSON), driven by the
    reviewed evaluation. Falls back to quality feedback when a re-upload is needed."""
    test = await get_test(db, sheet.test_id)
    can_reupload = (
        sheet.current_status in ("needs_reupload", "failed")
        and sheet.upload_attempt_number < MAX_UPLOAD_ATTEMPTS
    )
    status = _submission_status(sheet)

    quality = None
    if sheet.current_status in ("needs_reupload", "failed"):
        qc = await _latest_quality(db, sheet.id)
        if qc:
            quality = {
                "overall_status": qc.overall_status,
                "readability_score": qc.readability_score,
                "quality_notes": qc.quality_notes,
            }

    result: dict = {
        "sheet_id": sheet.id,
        "test_id": sheet.test_id,
        "display_name": test.display_name if test else "",
        "status": status,
        "upload_attempt_number": sheet.upload_attempt_number,
        "can_reupload": can_reupload,
        "quality": quality,
        "questions": [],
        "checked_pdf_url": None,
        "total_marks_awarded": None,
        "total_marks_possible": None,
    }

    evaluation = await _latest_evaluation(db, sheet.id)
    if evaluation and sheet.current_status == "checked":
        questions = await get_test_questions(db, sheet.test_id)
        by_number = {q.question_number: q for q in questions}
        rows = []
        for item in (evaluation.evaluation_data or {}).get("question_results", []):
            qnum = str(item.get("question_number") or "")
            q = by_number.get(qnum)
            rows.append({
                "question_number": qnum or (q.question_number if q else "?"),
                "question_text": q.question_text if q else "",
                "marks_awarded": float(item.get("awarded_marks", 0) or 0),
                "marks_possible": float(item.get("max_marks", q.marks if q else 0) or 0),
                "feedback": item.get("feedback"),
                "mistakes": item.get("missing_points") or [],
            })
        result["questions"] = rows
        result["total_marks_awarded"] = evaluation.total_marks_awarded
        result["total_marks_possible"] = evaluation.total_marks_possible

        annotation = await _latest_annotation(db, sheet.id)
        if annotation:
            result["checked_pdf_url"] = await signed_url(db, annotation.checked_file_id)

    return result


# ── Knowledge fetch (skill-generation only, best-effort) ─────────────────────────

async def fetch_question_resources(
    db: AsyncSession, *, topic: str | None, subtopic: str | None, query: str, top_k: int = 6,
) -> str:
    """Vector-search the SUBJECTIVE knowledge set for a question's topic/subtopic and
    return distilled excerpt text. Best-effort: returns "" on any failure or when no
    knowledge is uploaded, so skill generation never blocks (CLAUDE.md §11 — knowledge
    is used ONLY here at skill-generation time, never during per-sheet checking)."""
    from app.modules.knowledge.models import KnowledgeChunk
    try:
        from app.ai.model_router import get_provider
        from app.integrations.pinecone_client import get_pinecone

        embeddings = await get_provider("reasoning").embed([query[:6000]])
        if not embeddings:
            return ""
        filter_dict: dict = {"content_usage_type": "subjective"}
        if topic:
            filter_dict["topic"] = topic
        if subtopic:
            filter_dict["subtopic"] = {"$in": [subtopic]}

        matches = get_pinecone().query(embeddings[0], top_k=top_k, filter_dict=filter_dict)
        vector_ids = [m["id"] for m in matches if m.get("id")]
        if not vector_ids:
            return ""
        r = await db.execute(
            select(KnowledgeChunk).where(KnowledgeChunk.pinecone_vector_id.in_(vector_ids))
        )
        chunks = {c.pinecone_vector_id: c for c in r.scalars().all()}
        lines: list[str] = []
        for vid in vector_ids:
            c = chunks.get(vid)
            if not c:
                continue
            label = " | ".join(filter(None, [c.topic, c.subtopic])) or "General"
            lines.append(f"[{label}]\n{c.content}")
        return "\n\n".join(lines)
    except Exception as exc:
        logger.warning("question-resource retrieval failed (continuing without it): %s", exc)
        return ""


# ── Question-wise assembly + marks clamp (AI-free, used by the task) ──────────────

def assemble_questionwise(page_outputs: list[dict], questions: list[SubjectiveQuestion]) -> dict:
    """Assemble per-page question-level extractor outputs into whole-question answers.

    `page_outputs` is a list of extractor results, each {page, page_size:[w,h],
    answers:[{question_number, answer_text, question_bbox, continues}]}. Groups by
    question number (digit-tolerant), carrying an unlabeled continuation onto the last
    known question. Returns the stored extraction shape:
      {"pages": [{page, width, height}],
       "questions": [{qid, question_text, marks, answer_text, page_numbers,
                      page_regions: [{page, question_bbox}]}]}
    """
    valid_numbers = [q.question_number for q in questions]
    buckets: dict[str, dict] = {
        n: {"texts": [], "pages": set(), "regions": []} for n in valid_numbers
    }
    last_known: str | None = valid_numbers[0] if valid_numbers else None
    # When an answer is flagged as continuing onto the next page, an unlabeled answer
    # at the start of the next page belongs to it (it was the last thing written).
    pending_continuation: str | None = None

    pages_meta: list[dict] = []
    for po in page_outputs:
        size = po.get("page_size") or [0, 0]
        pages_meta.append({"page": po.get("page"), "width": size[0], "height": size[1]})
        for a in po.get("answers", []) or []:
            qid = _match_question_number(str(a.get("question_number") or ""), valid_numbers)
            if qid:
                last_known = qid
                target = qid
            else:
                target = pending_continuation or last_known
            pending_continuation = target if a.get("continues") else None
            if not target or target not in buckets:
                continue
            if a.get("answer_text"):
                buckets[target]["texts"].append(a["answer_text"])
            buckets[target]["pages"].add(po.get("page"))
            if a.get("question_bbox"):
                buckets[target]["regions"].append(
                    {"page": po.get("page"), "question_bbox": a["question_bbox"]}
                )

    out_questions = []
    for q in questions:
        b = buckets.get(q.question_number, {"texts": [], "pages": set(), "regions": []})
        out_questions.append({
            "qid": q.question_number,
            "question_text": q.question_text,
            "marks": q.marks,
            "answer_text": "\n".join(t for t in b["texts"] if t).strip(),
            "page_numbers": sorted(p for p in b["pages"] if p is not None),
            "page_regions": b["regions"],
        })
    return {"pages": pages_meta, "questions": out_questions}


def _match_question_number(raw: str, valid: list[str]) -> str | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw in valid:
        return raw
    # Loose match on the trailing digits (handles "Q1" vs "1" vs "Q. 1").
    raw_digits = "".join(ch for ch in raw if ch.isdigit())
    if not raw_digits:
        return None
    for v in valid:
        if "".join(ch for ch in v if ch.isdigit()) == raw_digits:
            return v
    return None


def clamp_marks(evaluation: dict, questions: list[SubjectiveQuestion]) -> tuple[dict, float, float]:
    """Enforce per-question full-marks caps and recompute totals on the checker/reviewer
    evaluation ({"question_results": [{question_number, awarded_marks, max_marks, ...}]}).
    Returns the sanitized evaluation plus (awarded, possible). Idempotent — safe to call
    after both the checker and the reviewer pass."""
    by_number = {q.question_number: q for q in questions}
    total_possible = float(sum(q.marks for q in questions))
    total_awarded = 0.0

    items = (evaluation or {}).get("question_results", [])
    for item in items:
        qnum = _match_question_number(str(item.get("question_number") or ""), list(by_number.keys()))
        q = by_number.get(qnum) if qnum else None
        cap = float(q.marks) if q else float(item.get("max_marks", 0) or 0)
        item["max_marks"] = cap
        if qnum:
            item["question_number"] = qnum
        awarded = float(item.get("awarded_marks", 0) or 0)
        awarded = max(0.0, min(awarded, cap))
        awarded = round(awarded * 2) / 2  # snap to nearest half mark (examiner convention)
        item["awarded_marks"] = awarded
        total_awarded += awarded

    evaluation["question_results"] = items
    total_awarded = round(total_awarded, 2)
    total_possible = round(total_possible, 2)
    evaluation["total_awarded_marks"] = total_awarded
    evaluation["total_full_marks"] = total_possible
    return evaluation, total_awarded, total_possible


# ── internal helpers ────────────────────────────────────────────────────────────

async def _student_sheets_by_test(db: AsyncSession, student_id: uuid.UUID) -> dict[uuid.UUID, StudentAnswerSheet]:
    r = await db.execute(
        select(StudentAnswerSheet)
        .where(StudentAnswerSheet.student_id == student_id)
        .order_by(StudentAnswerSheet.upload_attempt_number.asc())
    )
    # Latest attempt wins (ascending order → last write per test_id is highest).
    out: dict[uuid.UUID, StudentAnswerSheet] = {}
    for s in r.scalars().all():
        out[s.test_id] = s
    return out


def _submission_status(sheet: StudentAnswerSheet | None) -> str:
    if not sheet:
        return "none"
    if sheet.current_status == "checked":
        return "checked"
    if sheet.current_status == "needs_reupload":
        return "needs_reupload"
    if sheet.current_status == "failed":
        return "failed"
    return "processing"


async def _latest_quality(db: AsyncSession, sheet_id: uuid.UUID) -> AnswerQualityCheck | None:
    r = await db.execute(
        select(AnswerQualityCheck).where(AnswerQualityCheck.sheet_id == sheet_id)
        .order_by(AnswerQualityCheck.created_at.desc()).limit(1)
    )
    return r.scalar_one_or_none()


async def _latest_evaluation(db: AsyncSession, sheet_id: uuid.UUID) -> AnswerEvaluation | None:
    r = await db.execute(
        select(AnswerEvaluation).where(AnswerEvaluation.sheet_id == sheet_id)
        .order_by(AnswerEvaluation.created_at.desc()).limit(1)
    )
    return r.scalar_one_or_none()


async def _latest_annotation(db: AsyncSession, sheet_id: uuid.UUID) -> PDFAnnotation | None:
    r = await db.execute(
        select(PDFAnnotation).where(PDFAnnotation.sheet_id == sheet_id)
        .order_by(PDFAnnotation.created_at.desc()).limit(1)
    )
    return r.scalar_one_or_none()
