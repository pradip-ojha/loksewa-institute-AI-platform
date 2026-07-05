"""Subjective test + answer-sheet checking service.

Holds DB queries, signed-URL helpers, question-wise reconstruction, and
result-building. The heavy AI pipeline lives in the Celery task
(`workers/tasks/subjective_tasks.py`) which calls the agents in
`app/ai/agents/`; this module is the shared, AI-free logic both the router and
the task rely on.

Source-of-truth rule: per-question `marks` on SubjectiveQuestion is the hard cap
for awardable marks — enforced in `clamp_marks` regardless of what the AI returns.
"""
import asyncio
import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.integrations.r2_client import get_r2
from app.modules.ai_audit.models import AIOutput, AIRequest
from app.modules.files.models import File
from app.modules.jobs.models import ProcessingJob
from app.modules.subjective.models import (
    AnswerEvaluation, AnswerExtraction, AnswerQualityCheck, PDFAnnotation,
    QuestionSpecificCheckingSkill, StudentAnswerSheet, SubjectiveFeedbackChat,
    SubjectiveFeedbackMessage, SubjectiveQuestion, SubjectiveTest,
)
from app.modules.users.models import User

logger = logging.getLogger(__name__)

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
    from app.modules.exams.service import get_enrolled_exam_ids
    enrolled = await get_enrolled_exam_ids(db, student_id)
    sheets = await _student_sheets_by_test(db, student_id)
    test_ids = set(sheets.keys())

    cond = (SubjectiveTest.status == "active") & SubjectiveTest.exam_id.in_(enrolled or [uuid.uuid4()])
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
    # Show marks + feedback as soon as they're ready (feedback_ready), even while the
    # checked PDF is still being annotated. The checked-PDF URL stays None until the
    # annotation row exists (status `checked`).
    if evaluation and sheet.current_status in ("feedback_ready", "checked"):
        questions = await get_test_questions(db, sheet.test_id)
        by_number = {q.question_number: q for q in questions}
        rows = []
        for item in (evaluation.evaluation_data or {}).get("question_results", []):
            qnum = str(item.get("question_number") or "")
            q = by_number.get(qnum)
            sections = []
            for s in item.get("sections") or []:
                if not isinstance(s, dict):
                    continue
                sections.append({
                    "section": str(s.get("section") or ""),
                    "awarded": float(s.get("awarded_marks", 0) or 0),
                    "max": float(s.get("max_marks", 0) or 0),
                    "status": s.get("status") or "partial",
                    "note": str(s.get("note") or ""),
                })
            rows.append({
                "question_number": qnum or (q.question_number if q else "?"),
                "question_text": q.question_text if q else "",
                "marks_awarded": float(item.get("awarded_marks", 0) or 0),
                "marks_possible": float(item.get("max_marks", q.marks if q else 0) or 0),
                "feedback": item.get("feedback"),
                "mistakes": item.get("missing_points") or [],
                "sections": sections,
            })
        result["questions"] = rows
        result["total_marks_awarded"] = evaluation.total_marks_awarded
        result["total_marks_possible"] = evaluation.total_marks_possible

        annotation = await _latest_annotation(db, sheet.id)
        if annotation:
            result["checked_pdf_url"] = await signed_url(db, annotation.checked_file_id)

    return result


# ── Answer-sheet feedback chatbot (synchronous, explains the stored result) ──────

MAX_FEEDBACK_HISTORY = 10
_FEEDBACK_GREETING = (
    "नमस्ते! तपाईंको जाँचिएको उत्तरपुस्तिकाबारे जे पनि सोध्नुहोस् — किन यति अंक आयो, "
    "कसरी सुधार्ने, के बुँदा छुट्यो, वा कुनै बुँदा थपेको भए के हुन्थ्यो। म तपाईंको "
    "शिक्षकजस्तै बुझाउँछु।"
)


async def _load_feedback_chat(db: AsyncSession, chat_id: uuid.UUID) -> SubjectiveFeedbackChat | None:
    r = await db.execute(select(SubjectiveFeedbackChat).where(SubjectiveFeedbackChat.id == chat_id))
    return r.scalar_one_or_none()


async def _feedback_messages(db: AsyncSession, chat_id: uuid.UUID) -> list[SubjectiveFeedbackMessage]:
    r = await db.execute(
        select(SubjectiveFeedbackMessage)
        .where(SubjectiveFeedbackMessage.chat_id == chat_id)
        .order_by(SubjectiveFeedbackMessage.created_at.asc())
    )
    return list(r.scalars().all())


def _ensure_checked(sheet: StudentAnswerSheet) -> None:
    """Feedback chat unlocks as soon as the result + feedback are ready (after the
    reviewer pass), even while the checked PDF is still being annotated — its context
    (questions, extracted answer, skills, evaluation) is all available pre-annotation."""
    if sheet.current_status not in ("feedback_ready", "checked"):
        raise AppException(
            409, "not_checked",
            "You can ask follow-up questions only after your answer sheet has been checked.",
        )


async def build_feedback_context(
    db: AsyncSession, sheet: StudentAnswerSheet, include_qnums: set[str] | None = None,
) -> str:
    """Assemble the grounding context for the feedback chatbot from stored data ONLY
    (no re-extraction, no Pinecone): per question the configured marks + the reviewed
    evaluation (awarded, feedback, missing points, section breakdown), the student's
    extracted answer text, and the locked per-question checking guide. The chatbot
    explains this — it never re-grades.

    `include_qnums` (when given) restricts the heavy PER-QUESTION detail to just those
    question numbers — the selector agent (§12.1) decides which the student's message
    actually needs, so we don't ship every question's guide on every turn. The test
    header, overall result, and personalization block are always included. None = all
    questions (used for the greeting and broad questions)."""
    import json

    test = await get_test(db, sheet.test_id)
    questions = await get_test_questions(db, sheet.test_id)
    evaluation = await _latest_evaluation(db, sheet.id)
    eval_by_num: dict[str, dict] = {}
    if evaluation and isinstance(evaluation.evaluation_data, dict):
        for item in evaluation.evaluation_data.get("question_results", []) or []:
            eval_by_num[str(item.get("question_number") or "")] = item

    # Student's extracted answer text, keyed by question id.
    extraction = (
        await db.execute(
            select(AnswerExtraction).where(AnswerExtraction.sheet_id == sheet.id)
            .order_by(AnswerExtraction.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    answer_by_qid: dict[str, str] = {}
    if extraction and isinstance(extraction.extracted_data, dict):
        for a in extraction.extracted_data.get("questions", []) or []:
            answer_by_qid[str(a.get("qid") or "")] = str(a.get("answer_text") or "")

    # Locked per-question checking guide, keyed by question id.
    sk_r = await db.execute(
        select(QuestionSpecificCheckingSkill).where(
            QuestionSpecificCheckingSkill.test_id == sheet.test_id,
            QuestionSpecificCheckingSkill.is_active.is_(True),
        )
    )
    skill_by_qid = {sk.question_id: sk.skill_json for sk in sk_r.scalars().all()}

    # 5th input (spec §5.2): personalization — who this student is + their subjective-mock
    # mistake history. Best-effort; the chatbot still only EXPLAINS the stored result.
    person = ""
    try:
        from app.modules.personalization import service as pers
        person = await pers.build_subjective_feedback_context(db, sheet.student_id)
    except Exception:
        person = ""

    lines: list[str] = []
    if person:
        lines.append("STUDENT CONTEXT (personalization — for tone/emphasis only; never changes the marks):")
        lines.append(person)
        lines.append("")
    if test:
        lines.append(f"TEST: {test.display_name}")
        if test.custom_instruction:
            lines.append(f"ADMIN CHECKING INSTRUCTION: {test.custom_instruction}")
    if evaluation:
        lines.append(
            f"OVERALL: {evaluation.total_marks_awarded} / {evaluation.total_marks_possible}"
        )
        summary = (evaluation.evaluation_data or {}).get("overall_summary")
        if summary:
            lines.append(f"OVERALL SUMMARY: {summary}")

    for q in questions:
        # When the selector narrowed to specific questions, only emit those questions'
        # heavy detail (digit-tolerant match). The header/overall/personalization above stay.
        if include_qnums is not None and not _match_question_number(q.question_number, list(include_qnums)):
            continue
        item = eval_by_num.get(q.question_number) or _match_eval_item(q.question_number, eval_by_num)
        lines.append("\n" + "─" * 8)
        lines.append(f"QUESTION {q.question_number} (max {q.marks} marks): {q.question_text}")
        ans = answer_by_qid.get(q.question_number) or _match_answer(q.question_number, answer_by_qid)
        lines.append(f"STUDENT'S WRITTEN ANSWER (transcribed): {ans or '(not captured)'}")
        if item:
            lines.append(f"AWARDED: {item.get('awarded_marks')} / {item.get('max_marks', q.marks)}")
            if item.get("feedback"):
                lines.append(f"EXAMINER FEEDBACK: {item['feedback']}")
            missing = item.get("missing_points") or []
            if missing:
                lines.append("MISSING POINTS: " + "; ".join(str(m) for m in missing))
            sections = item.get("sections") or []
            if sections:
                lines.append("SECTION BREAKDOWN:")
                for s in sections:
                    if not isinstance(s, dict):
                        continue
                    lines.append(
                        f"  - {s.get('section')}: {s.get('awarded_marks')}/{s.get('max_marks')} "
                        f"[{s.get('status')}] — {s.get('note') or ''}"
                    )
        guide = skill_by_qid.get(q.id)
        if guide:
            lines.append("CHECKING GUIDE (how this question is marked): " + json.dumps(guide, ensure_ascii=False)[:4000])

    return "\n".join(lines)


async def _build_question_index(db: AsyncSession, sheet: StudentAnswerSheet) -> str:
    """A compact one-line-per-question listing (number — short text — awarded/max) for the
    feedback selector to route on. Cheap: no guides, no full answers."""
    questions = await get_test_questions(db, sheet.test_id)
    evaluation = await _latest_evaluation(db, sheet.id)
    eval_by_num: dict[str, dict] = {}
    if evaluation and isinstance(evaluation.evaluation_data, dict):
        for item in evaluation.evaluation_data.get("question_results", []) or []:
            eval_by_num[str(item.get("question_number") or "")] = item
    lines: list[str] = []
    for q in questions:
        item = eval_by_num.get(q.question_number) or _match_eval_item(q.question_number, eval_by_num)
        awarded = item.get("awarded_marks") if item else "?"
        text = (q.question_text or "").strip().replace("\n", " ")[:90]
        lines.append(f"{q.question_number} — {text} — {awarded}/{q.marks}")
    return "\n".join(lines)


def _match_eval_item(qnum: str, eval_by_num: dict[str, dict]) -> dict | None:
    matched = _match_question_number(qnum, list(eval_by_num.keys()))
    return eval_by_num.get(matched) if matched else None


def _match_answer(qnum: str, answer_by_qid: dict[str, str]) -> str:
    matched = _match_question_number(qnum, list(answer_by_qid.keys()))
    return answer_by_qid.get(matched, "") if matched else ""


async def start_feedback_chat(db: AsyncSession, sheet_id: uuid.UUID, student_id: uuid.UUID) -> dict:
    """Open (or resume) the feedback chat for a checked sheet the student owns, seeded
    with a greeting. Resumes the latest open chat so history isn't lost on reload."""
    sheet = await get_sheet(db, sheet_id)
    if not sheet or sheet.student_id != student_id:
        raise AppException(404, "not_found", "Answer sheet not found.")
    _ensure_checked(sheet)

    existing = (
        await db.execute(
            select(SubjectiveFeedbackChat)
            .where(
                SubjectiveFeedbackChat.sheet_id == sheet_id,
                SubjectiveFeedbackChat.student_id == student_id,
                SubjectiveFeedbackChat.status == "open",
            )
            .order_by(SubjectiveFeedbackChat.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing:
        msgs = await _feedback_messages(db, existing.id)
        return {
            "chat_id": existing.id,
            "messages": [{"role": m.role, "content": m.content} for m in msgs],
        }

    chat = SubjectiveFeedbackChat(sheet_id=sheet_id, student_id=student_id, status="open")
    db.add(chat)
    await db.flush()
    db.add(SubjectiveFeedbackMessage(chat_id=chat.id, role="assistant", content=_FEEDBACK_GREETING))
    await db.commit()
    return {"chat_id": chat.id, "messages": [{"role": "assistant", "content": _FEEDBACK_GREETING}]}


async def post_feedback_question(
    db: AsyncSession, sheet_id: uuid.UUID, chat_id: uuid.UUID, question: str, student_id: uuid.UUID,
) -> dict:
    """Persist the student turn, run the feedback agent against the stored evaluation
    context, persist the reply, and return the full transcript + follow-up suggestions."""
    from app.ai.agents.answer_feedback_chat_agent import AnswerFeedbackChatAgent

    sheet = await get_sheet(db, sheet_id)
    if not sheet or sheet.student_id != student_id:
        raise AppException(404, "not_found", "Answer sheet not found.")
    _ensure_checked(sheet)

    chat = await _load_feedback_chat(db, chat_id)
    if not chat or chat.sheet_id != sheet_id or chat.student_id != student_id:
        raise AppException(404, "chat_not_found", "Feedback chat not found.")

    prior = await _feedback_messages(db, chat_id)
    history = "\n".join(
        f"{'STUDENT' if m.role == 'student' else 'TUTOR'}: {m.content}"
        for m in prior[-MAX_FEEDBACK_HISTORY:]
    )

    db.add(SubjectiveFeedbackMessage(chat_id=chat_id, role="student", content=question))
    await db.flush()

    # A cheap selector (§12.1) first picks which question(s) the student's message is
    # about, so build_feedback_context only ships those questions' heavy grading detail
    # instead of every question's on every turn. Fail-open: broad/unsure → full context.
    from app.ai.agents.answer_feedback_selector_agent import AnswerFeedbackSelectorAgent
    q_index = await _build_question_index(db, sheet)
    selection = await AnswerFeedbackSelectorAgent(db).select(
        question=question, question_index=q_index, history=history, sheet_id=sheet.id,
    )
    include = None if selection["needs_all"] else set(selection["question_numbers"])
    context = await build_feedback_context(db, sheet, include_qnums=include)
    agent = AnswerFeedbackChatAgent(db)
    result = await agent.answer(
        question=question, evaluation_context=context, history=history, sheet_id=sheet.id,
    )

    db.add(SubjectiveFeedbackMessage(chat_id=chat_id, role="assistant", content=result["reply"]))
    await db.commit()

    # Personalization: roll this turn into the chat-session summary (best-effort).
    try:
        from app.core.celery_client import get_celery
        turns = f"{history}\nSTUDENT: {question}\nTUTOR: {result['reply']}"[:8000]
        get_celery().send_task(
            "workers.tasks.personalization_tasks.pers_update_chat",
            args=[str(student_id), "subjective_feedback", str(chat_id), turns], queue="kvi_ai_default",
        )
    except Exception:  # noqa: BLE001
        pass

    messages = [{"role": m.role, "content": m.content} for m in prior]
    messages.append({"role": "student", "content": question})
    messages.append({"role": "assistant", "content": result["reply"]})
    return {
        "chat_id": chat_id,
        "messages": messages,
        "follow_up_suggestions": result["follow_up_suggestions"],
    }


async def get_feedback_chat(db: AsyncSession, sheet_id: uuid.UUID, student_id: uuid.UUID) -> dict:
    """Latest open feedback chat for a sheet the student owns (for UI reload). Returns
    an empty transcript when no chat has been started yet."""
    sheet = await get_sheet(db, sheet_id)
    if not sheet or sheet.student_id != student_id:
        raise AppException(404, "not_found", "Answer sheet not found.")
    chat = (
        await db.execute(
            select(SubjectiveFeedbackChat)
            .where(
                SubjectiveFeedbackChat.sheet_id == sheet_id,
                SubjectiveFeedbackChat.student_id == student_id,
                SubjectiveFeedbackChat.status == "open",
            )
            .order_by(SubjectiveFeedbackChat.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if not chat:
        return {"chat_id": None, "messages": []}
    msgs = await _feedback_messages(db, chat.id)
    return {"chat_id": chat.id, "messages": [{"role": m.role, "content": m.content} for m in msgs]}


# ── Knowledge fetch (skill-generation only, best-effort) ─────────────────────────

async def fetch_question_resources(
    db: AsyncSession, *, exam_id, topic: str | None, subtopic: str | None, query: str,
    chapter: str | None = None, top_k: int = 6,
) -> str:
    """Vector-search the exam's knowledge set for a question's chapter/topic/subtopic and
    return distilled excerpt text. Always filtered by `exam_id` so retrieval never crosses
    exams, and by `chapter` (the PRIMARY retrieval dimension, CLAUDE.md §8) when known so it
    never crosses chapters within an exam; topic/subtopic narrow within the chapter.
    Best-effort: returns "" on any failure or when no knowledge is uploaded, so skill
    generation never blocks (CLAUDE.md §11 — knowledge is used ONLY here at skill-generation
    time, never during per-sheet checking). Runs a dual (prose + model_qa) query so model-answer
    pairs are retrieved alongside prose (CLAUDE.md §8)."""
    try:
        from app.ai.model_router import get_provider
        from app.modules.knowledge.retrieval import query_knowledge_dual

        embeddings = await get_provider("reasoning").embed([query[:6000]])
        if not embeddings:
            return ""
        filter_dict: dict = {"exam_id": str(exam_id)}
        if chapter:
            filter_dict["chapter"] = chapter
        if topic:
            filter_dict["topic"] = topic
        if subtopic:
            filter_dict["subtopic"] = {"$in": [subtopic]}

        hits = await query_knowledge_dual(
            db, embedding=embeddings[0], base_filter=filter_dict, prose_top_k=top_k,
        )
        if not hits:
            return ""
        lines: list[str] = []
        for h in hits:
            if h.is_qa:
                lines.append(f"[Model Q&A — प्रश्न: {h.question or 'General'}]\n{h.content}")
            else:
                label = " | ".join(filter(None, [h.topic, h.subtopic])) or "General"
                lines.append(f"[{label}]\n{h.content}")
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
                      page_regions: [{page, question_bbox, answer_text}]}]}
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
                    {"page": po.get("page"), "question_bbox": a["question_bbox"],
                     "answer_text": a.get("answer_text") or ""}
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


def _half(v: float) -> float:
    return round(v * 2) / 2


def _clamp_sections(item: dict, question_cap: float) -> None:
    """Make the section breakdown complete and consistent with the question:
    clamp each section's awarded to its max, ensure the section MAXES cover the full
    question marks (add a remainder section if the breakdown is short), and force the
    section AWARDED sum to equal the question's awarded_marks. Mutates in place; the
    question's awarded_marks stays the source of truth."""
    sections = item.get("sections")
    if not isinstance(sections, list) or not sections:
        return

    q_awarded = _half(max(0.0, min(float(item.get("awarded_marks", 0) or 0), question_cap)))

    clean: list[dict] = []
    for s in sections:
        if not isinstance(s, dict):
            continue
        smax = _half(max(0.0, float(s.get("max_marks", 0) or 0)))
        sa = _half(max(0.0, min(float(s.get("awarded_marks", 0) or 0), smax)))
        s["max_marks"] = smax
        s["awarded_marks"] = sa
        clean.append(s)
    if not clean:
        return

    # 1) Ensure the section maxes cover the full question marks (complete breakdown).
    sum_max = sum(s["max_marks"] for s in clean)
    if sum_max < question_cap - 1e-6:
        clean.append({
            "section": "अन्य", "max_marks": _half(question_cap - sum_max),
            "awarded_marks": 0.0, "status": "wrong", "evidence_text": "",
            "note": "यो भागको लागि अपेक्षित उत्तर लेखिएको छैन — यो थप्नुपर्छ।",
        })

    # 2) Force the awarded section sum to equal the question's awarded marks.
    diff = _half(q_awarded - sum(s["awarded_marks"] for s in clean))
    if diff > 0:  # need to add marks — give to sections with the most headroom first
        for s in sorted(clean, key=lambda x: x["max_marks"] - x["awarded_marks"], reverse=True):
            room = s["max_marks"] - s["awarded_marks"]
            if room <= 0:
                continue
            add = min(diff, room)
            s["awarded_marks"] = _half(s["awarded_marks"] + add)
            diff = _half(diff - add)
            if diff <= 0:
                break
    elif diff < 0:  # need to remove marks — take from the most-awarded sections first
        for s in sorted(clean, key=lambda x: x["awarded_marks"], reverse=True):
            take = min(-diff, s["awarded_marks"])
            if take <= 0:
                continue
            s["awarded_marks"] = _half(s["awarded_marks"] - take)
            diff = _half(diff + take)
            if diff >= 0:
                break

    # 3) Recompute status from the reconciled marks.
    for s in clean:
        sa, smax = s["awarded_marks"], s["max_marks"]
        s["status"] = "correct" if (smax > 0 and sa >= smax) else ("partial" if sa > 0 else "wrong")

    item["sections"] = clean


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
        _clamp_sections(item, cap)
        total_awarded += awarded

    evaluation["question_results"] = items
    total_awarded = round(total_awarded, 2)
    total_possible = round(total_possible, 2)
    evaluation["total_awarded_marks"] = total_awarded
    evaluation["total_full_marks"] = total_possible
    return evaluation, total_awarded, total_possible


# ── Admin debug: full per-step pipeline trace (admin-only, for optimization) ──────

async def _job_info(db: AsyncSession, job_id: uuid.UUID | None) -> dict | None:
    if not job_id:
        return None
    r = await db.execute(select(ProcessingJob).where(ProcessingJob.id == job_id))
    j = r.scalar_one_or_none()
    if not j:
        return None
    return {
        "id": str(j.id),
        "job_type": j.job_type,
        "status": getattr(j.status, "value", j.status),
        "progress_percent": j.progress_percent,
        "current_step": j.current_step,
        "error_message": j.error_message,
        "input_reference": j.input_reference,
        "output_reference": j.output_reference,
        "created_at": j.created_at,
        "started_at": j.started_at,
        "completed_at": j.completed_at,
    }


async def _ai_calls(db: AsyncSession, entity_id: uuid.UUID) -> list[dict]:
    """Every AI call logged against this entity (sheet or test), in order — so the
    admin can see which agent ran, token cost, latency, status, and a short output
    summary for each step."""
    r = await db.execute(
        select(AIRequest, AIOutput.output_summary)
        .outerjoin(AIOutput, AIOutput.request_id == AIRequest.id)
        .where(AIRequest.related_entity_id == entity_id)
        .order_by(AIRequest.created_at.asc())
    )
    out: list[dict] = []
    for req, summary in r.all():
        out.append({
            "agent_type": req.agent_type,
            "task_type": req.task_type,
            "provider": req.provider,
            "model": req.model,
            "status": req.status,
            "input_tokens": req.input_tokens,
            "output_tokens": req.output_tokens,
            "latency_ms": req.latency_ms,
            "error_message": req.error_message,
            "output_summary": summary,
            "created_at": req.created_at,
        })
    return out


async def build_sheet_debug(db: AsyncSession, sheet_id: uuid.UUID) -> dict | None:
    """Full step-by-step trace of the answer-sheet checking pipeline for one sheet:
    quality gate → question-level extraction → locked skills used → checker (initial)
    → reviewer (final) → annotation locator + geometry validation → draw commands →
    checked PDF, plus every AI call. Admin-only; exposes internal JSON for tuning."""
    sheet = await get_sheet(db, sheet_id)
    if not sheet:
        return None
    test = await get_test(db, sheet.test_id)
    student = (
        await db.execute(select(User).where(User.id == sheet.student_id))
    ).scalar_one_or_none()

    qc = await _latest_quality(db, sheet.id)
    extraction = (
        await db.execute(
            select(AnswerExtraction).where(AnswerExtraction.sheet_id == sheet.id)
            .order_by(AnswerExtraction.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    evaluation = await _latest_evaluation(db, sheet.id)
    annotation = await _latest_annotation(db, sheet.id)

    # The locked per-question checking skills the checker consumed for this test.
    sk_r = await db.execute(
        select(QuestionSpecificCheckingSkill, SubjectiveQuestion.question_number)
        .join(SubjectiveQuestion, SubjectiveQuestion.id == QuestionSpecificCheckingSkill.question_id)
        .where(QuestionSpecificCheckingSkill.test_id == sheet.test_id)
        .order_by(SubjectiveQuestion.question_order)
    )
    locked_skills = [
        {
            "question_number": qnum,
            "evaluation_status": sk.evaluation_status,
            "evaluation_notes": sk.evaluation_notes,
            "iterations": sk.iterations,
            "skill_json": sk.skill_json,
        }
        for sk, qnum in sk_r.all()
    ]

    return {
        "sheet": {
            "sheet_id": str(sheet.id),
            "test_id": str(sheet.test_id),
            "test_name": test.display_name if test else None,
            "student_name": student.full_name if student else None,
            "student_email": student.email if student else None,
            "upload_attempt_number": sheet.upload_attempt_number,
            "current_status": sheet.current_status,
            "answer_sheet_url": await signed_url(db, sheet.file_id),
            "created_at": sheet.created_at,
        },
        "job": await _job_info(db, sheet.checking_job_id),
        "steps": {
            "1_quality_check": (
                {
                    "blur_score": qc.blur_score,
                    "brightness_score": qc.brightness_score,
                    "tilt_angle": qc.tilt_angle,
                    "resolution_ok": qc.resolution_ok,
                    "readability_score": qc.readability_score,
                    "overall_status": qc.overall_status,
                    "quality_notes": qc.quality_notes,
                }
                if qc else None
            ),
            "2_extraction": (
                {
                    "model_used": extraction.model_used,
                    "overall_confidence": extraction.overall_confidence,
                    "extracted_data": extraction.extracted_data,
                }
                if extraction else None
            ),
            "3_locked_skills_used": locked_skills,
            "4_checker_initial_evaluation": (
                evaluation.initial_evaluation_data if evaluation else None
            ),
            "5_reviewer_final_evaluation": (
                {
                    "reviewed": evaluation.reviewed,
                    "review_notes": evaluation.review_notes,
                    "total_marks_awarded": evaluation.total_marks_awarded,
                    "total_marks_possible": evaluation.total_marks_possible,
                    "overall_confidence": evaluation.overall_confidence,
                    "evaluation_data": evaluation.evaluation_data,
                }
                if evaluation else None
            ),
            "6_locator_and_validation": (
                annotation.locator_plan if annotation else None
            ),
            "7_annotation_commands": (
                annotation.annotation_instructions if annotation else None
            ),
            "8_checked_pdf_url": (
                await signed_url(db, annotation.checked_file_id) if annotation else None
            ),
        },
        "ai_calls": await _ai_calls(db, sheet.id),
    }


async def build_skill_debug(db: AsyncSession, test_id: uuid.UUID) -> dict | None:
    """Full step-by-step trace of the question-paper → checking-skill generation
    workflow for one test: extracted questions + marks, detected topic/subtopic, and
    the locked per-question checking guide with its evaluator verdict + iterations,
    plus every AI call. Admin-only."""
    test = await get_test(db, test_id)
    if not test:
        return None
    questions = await get_test_questions(db, test_id)

    sk_r = await db.execute(
        select(QuestionSpecificCheckingSkill, SubjectiveQuestion.question_number)
        .join(SubjectiveQuestion, SubjectiveQuestion.id == QuestionSpecificCheckingSkill.question_id)
        .where(QuestionSpecificCheckingSkill.test_id == test_id)
        .order_by(SubjectiveQuestion.question_order)
    )
    skills_by_qnum: dict[str, dict] = {}
    for sk, qnum in sk_r.all():
        skills_by_qnum[qnum] = {
            "version": sk.version,
            "evaluation_status": sk.evaluation_status,
            "evaluation_notes": sk.evaluation_notes,
            "iterations": sk.iterations,
            "skill_json": sk.skill_json,
        }

    return {
        "test": {
            "test_id": str(test.id),
            "display_name": test.display_name,
            "status": test.status,
            "skill_generation_status": test.skill_generation_status,
            "num_questions": test.num_questions,
            "total_marks": test.total_marks,
            "custom_instruction": test.custom_instruction,
            "has_rubric": test.rubric_file_id is not None,
            "question_paper_url": await signed_url(db, test.question_paper_file_id),
            "model_answer_url": await signed_url(db, test.model_answer_file_id),
            "rubric_url": await signed_url(db, test.rubric_file_id),
        },
        "job": await _job_info(db, test.skill_generation_job_id),
        "steps": {
            "1_extracted_questions": [
                {
                    "question_number": q.question_number,
                    "question_text": q.question_text,
                    "marks": q.marks,
                    "question_order": q.question_order,
                    "detected_topic": q.topic,
                    "detected_subtopic": q.subtopic,
                }
                for q in questions
            ],
            "2_generated_and_locked_skills": [
                {
                    "question_number": q.question_number,
                    "marks": q.marks,
                    "topic": q.topic,
                    "subtopic": q.subtopic,
                    **(skills_by_qnum.get(q.question_number) or {"skill_json": None}),
                }
                for q in questions
            ],
        },
        "ai_calls": await _ai_calls(db, test.id),
    }


async def build_debug_pdf(db: AsyncSession, sheet_id: uuid.UUID) -> dict | None:
    """Coordinate debug: re-render the original answer sheet (deterministic, same pixel
    space as checking) and overlay the persisted locator/validation geometry — raw
    points/boxes vs. final validated geometry + page corners. Returns a signed URL to a
    diagnostic PDF so an annotation mismatch can be diagnosed as geometry vs. style.
    Admin-only."""
    import io as _io

    from app.processing import annotation_debug, pdf_tools

    sheet = await get_sheet(db, sheet_id)
    if not sheet:
        return None
    annotation = await _latest_annotation(db, sheet.id)
    plan_targets: list[dict] = []
    if annotation and isinstance(annotation.locator_plan, dict):
        plan_targets = annotation.locator_plan.get("targets") or []

    f = (await db.execute(select(File).where(File.id == sheet.file_id))).scalar_one_or_none()
    if not f:
        return None
    file_bytes = await asyncio.to_thread(get_r2().download_fileobj, f.r2_key)
    pages = pdf_tools.render_to_page_images(file_bytes, f.mime_type)

    # Group plans by their 1-based page number.
    by_page: dict[int, list[dict]] = {}
    for plan in plan_targets:
        if isinstance(plan, dict):
            by_page.setdefault(int(plan.get("page_number") or 1), []).append(plan)

    overlaid = [
        annotation_debug.draw_debug_overlay(p.png_bytes, by_page.get(p.page_number, []))
        for p in pages
    ]
    debug_pdf = pdf_tools.build_pdf_from_images(overlaid)

    debug_key = f"answer-sheets/debug/{sheet.id}/debug.pdf"
    get_r2().upload_fileobj(debug_key, _io.BytesIO(debug_pdf), "application/pdf")
    debug_file = File(
        original_filename="debug.pdf", display_name="Coordinate debug",
        mime_type="application/pdf", file_size=len(debug_pdf), r2_key=debug_key,
        uploaded_by=sheet.student_id,
    )
    db.add(debug_file)
    await db.commit()

    return {
        "sheet_id": str(sheet.id),
        "pages": len(pages),
        "targets": len(plan_targets),
        "debug_pdf_url": get_r2().get_signed_url(debug_key),
        "legend": {
            "cyan": "page corners", "blue": "raw locator underline points",
            "orange": "raw target_text_box", "green": "raw comment_box",
            "red": "final validated underline path", "magenta": "final comment box",
            "purple": "section evidence/tick/mark (positive marking)",
        },
    }


# ── orphan recovery (worker restart / dead worker) ───────────────────────────────

# Sheet statuses that mean "still being processed" (everything except the terminals).
_SHEET_IN_PROGRESS = ("uploaded", "extracting", "evaluating", "reviewing", "annotating")


async def fail_orphaned_sheets_and_tests(db: AsyncSession) -> int:
    """Mark in-progress answer sheets / tests as failed when their job is gone.

    Backstop for a worker that died mid-job (e.g. a restart): the job row is failed
    by the reaper / startup recovery, but the sheet's own `current_status` stays at
    'extracting'/'evaluating'/… forever, so the UI spins and the student can't
    re-upload. This reconciles those: any in-progress sheet whose checking job is
    failed/cancelled or missing becomes 'failed' (which enables re-upload); same for
    a test whose skill-generation job died. Returns how many were reconciled."""
    from app.modules.jobs.models import JobStatus

    reconciled = 0
    dead = {JobStatus.failed, JobStatus.cancelled}

    sheets = (await db.execute(
        select(StudentAnswerSheet).where(StudentAnswerSheet.current_status.in_(_SHEET_IN_PROGRESS))
    )).scalars().all()
    for sheet in sheets:
        job = None
        if sheet.checking_job_id:
            job = (await db.execute(
                select(ProcessingJob).where(ProcessingJob.id == sheet.checking_job_id)
            )).scalar_one_or_none()
        if job is None or job.status in dead:
            sheet.current_status = "failed"
            reconciled += 1

    # A sheet at `feedback_ready` already has its final marks + feedback; only the
    # checked PDF was outstanding. If its job died during the background annotation
    # phase, settle it to `checked` (results stand, PDF simply unavailable) — never
    # `failed`, which would hide a completed evaluation.
    fr_sheets = (await db.execute(
        select(StudentAnswerSheet).where(StudentAnswerSheet.current_status == "feedback_ready")
    )).scalars().all()
    for sheet in fr_sheets:
        job = None
        if sheet.checking_job_id:
            job = (await db.execute(
                select(ProcessingJob).where(ProcessingJob.id == sheet.checking_job_id)
            )).scalar_one_or_none()
        if job is None or job.status in dead:
            sheet.current_status = "checked"
            reconciled += 1

    tests = (await db.execute(
        select(SubjectiveTest).where(
            ~SubjectiveTest.skill_generation_status.in_(("completed", "failed"))
        )
    )).scalars().all()
    for t in tests:
        if not t.skill_generation_status:
            continue
        job = None
        if t.skill_generation_job_id:
            job = (await db.execute(
                select(ProcessingJob).where(ProcessingJob.id == t.skill_generation_job_id)
            )).scalar_one_or_none()
        if job is None or job.status in dead:
            t.skill_generation_status = "failed"
            reconciled += 1

    if reconciled:
        await db.commit()
    return reconciled


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
    # Result + feedback are ready; the checked PDF is still being annotated.
    if sheet.current_status == "feedback_ready":
        return "feedback_ready"
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
