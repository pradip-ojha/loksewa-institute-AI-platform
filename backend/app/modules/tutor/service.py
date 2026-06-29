"""Main AI Tutor service: a synchronous topic-selector → notes/book retrieval →
main-tutor chain, scoped to ONE exam (CLAUDE.md §13.1 tutor flow).

This is distinct from the video tutor: there is no video/timeline. The Topic Selector
Agent picks a chapter/topic/subtopic (validated HARD against the exam's live syllabus),
then the routed notes/book chunks are retrieved (reusing the video module's Pinecone
helper, exam-filtered) and the main tutor answers from that context only. Sessions are
ChatGPT-style — one session per ongoing conversation, scoped to the chosen exam.
"""
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.tutor.models import TutorChatMessage, TutorChatSession
from app.modules.video.service import fetch_supporting_knowledge, get_chapter_tree

logger = logging.getLogger(__name__)

MAX_TUTOR_HISTORY = 8


async def get_or_create_session(
    db: AsyncSession, student_id: uuid.UUID, exam_id: uuid.UUID, session_id: uuid.UUID | None,
) -> TutorChatSession:
    if session_id:
        r = await db.execute(
            select(TutorChatSession).where(
                TutorChatSession.id == session_id,
                TutorChatSession.student_id == student_id,
            )
        )
        existing = r.scalar_one_or_none()
        if existing:
            return existing
    session = TutorChatSession(student_id=student_id, exam_id=exam_id)
    db.add(session)
    await db.flush()
    return session


async def get_history(db: AsyncSession, student_id: uuid.UUID, session_id: uuid.UUID) -> list[TutorChatMessage]:
    r = await db.execute(
        select(TutorChatMessage)
        .where(TutorChatMessage.session_id == session_id, TutorChatMessage.student_id == student_id)
        .order_by(TutorChatMessage.created_at.asc())
    )
    return list(r.scalars().all())


async def _recent_history_text(db: AsyncSession, session_id: uuid.UUID) -> str:
    r = await db.execute(
        select(TutorChatMessage)
        .where(TutorChatMessage.session_id == session_id)
        .order_by(TutorChatMessage.created_at.desc())
        .limit(MAX_TUTOR_HISTORY)
    )
    msgs = list(r.scalars().all())[::-1]
    parts: list[str] = []
    for m in msgs:
        parts.append(f"STUDENT: {m.question}")
        if m.answer:
            parts.append(f"TUTOR: {m.answer}")
    return "\n".join(parts)


async def run_tutor_chain(
    db: AsyncSession, *, student_id: uuid.UUID, exam_id: uuid.UUID, question: str, session: TutorChatSession,
) -> dict:
    """Topic selector → exam-filtered notes/book retrieval → main tutor. Grounding stays
    inside the chosen exam; the selector's topic/subtopics are validated against the
    exam's live syllabus before any retrieval."""
    from app.ai.agents.tutor_agent import TutorAgent
    from app.ai.agents.tutor_topic_selector_agent import TutorTopicSelectorAgent

    from app.modules.personalization import service as pers

    tree_text, valid_topics, valid_subs, valid_chapters, topic_to_chapter = await get_chapter_tree(db, exam_id)
    history_text = await _recent_history_text(db, session.id)
    # Personalization context (global per student) + a compact recent-activity list the
    # selector can match the question against (activity-aware retrieval, spec §4.4).
    personalization = await pers.build_main_tutor_context(db, student_id)
    recent_acts, act_by_id = await _recent_activities(db, student_id)

    # ── Step 1: Topic Selector (exam tree + activity-aware) ─────────────────────
    detected_topic: str | None = None
    detected_subtopics: list[str] = []
    detected_chapter: str | None = None
    query_rewrite = ""
    confidence = 0.0
    target_activity_id = ""
    try:
        route = await TutorTopicSelectorAgent(db).select(
            question=question, syllabus_tree=tree_text, recent_activities=recent_acts,
            history=history_text, session_id=session.id,
        )
        query_rewrite = route.get("query_rewrite") or ""
        confidence = route.get("confidence") or 0.0
        target_activity_id = route.get("target_activity_id") or ""
        detected_topic, detected_subtopics, detected_chapter = _validate(
            route.get("topic"), route.get("subtopics") or [], route.get("chapter"),
            valid_topics, valid_subs, valid_chapters, topic_to_chapter,
        )
    except Exception as exc:
        logger.warning("tutor topic selection failed, using broad exam scope: %s", exc)

    retrieval_query = query_rewrite or question

    # ── Step 1b: Attach specific past-activity detail ONLY when the question targets it ─
    activity_detail = ""
    if target_activity_id and target_activity_id in act_by_id:
        try:
            activity_detail = await pers.get_activity_detail(db, student_id, act_by_id[target_activity_id])
        except Exception as exc:
            logger.warning("activity detail fetch failed: %s", exc)

    # ── Step 2: Fetch notes/book chunks for the routed scope (exam-filtered) ─────
    knowledge_text, supporting = await fetch_supporting_knowledge(
        db, exam_id=exam_id, chapter=detected_chapter, topic=detected_topic,
        subtopic_ids=detected_subtopics, question=retrieval_query,
    )

    # ── Step 3: Main Tutor answers from the retrieved content + student context ──
    person_block = "\n\n".join(p for p in [personalization, activity_detail] if p)
    answer = await TutorAgent(db).answer(
        question=question, scope="EXAM SYLLABUS:\n" + tree_text, knowledge_text=knowledge_text,
        history=history_text, session_id=session.id, personalization=person_block,
    )

    # ── Persist ──────────────────────────────────────────────────────────────────
    msg = TutorChatMessage(
        session_id=session.id,
        student_id=student_id,
        question=question,
        answer=answer["answer"],
        language=answer["language"],
        detected_topic=detected_topic,
        detected_subtopic_ids=detected_subtopics,
        query_rewrite=retrieval_query,
        supporting_knowledge_json=supporting,
        confidence=answer["confidence"],
        follow_up_suggestions=answer["follow_up_suggestions"],
    )
    db.add(msg)
    await db.commit()

    # Personalization: roll this turn into the session summary + daily summary (best-effort).
    _enqueue_chat_summary(student_id, session.id, history_text, question, answer["answer"])

    return {
        "answer": answer["answer"],
        "language": answer["language"],
        "chat_session_id": session.id,
        "detected_topic": detected_topic,
        "detected_subtopic_ids": detected_subtopics,
        "supporting_knowledge_used": supporting,
        "confidence": answer["confidence"],
        "selection_confidence": confidence,
        "follow_up_suggestions": answer["follow_up_suggestions"],
    }


def _validate(
    topic: str | None, subtopics: list[str], chapter: str | None,
    valid_topics: set[str], valid_subtopics: set[str],
    valid_chapters: set[str], topic_to_chapter: dict[str, str],
) -> tuple[str | None, list[str], str | None]:
    """Drop any topic/subtopic/chapter not present in the exam syllabus (never leave the
    exam). Chapter (PRIMARY retrieval dimension) is resolved deterministically from the
    validated topic; the selector's explicit chapter is used only when no topic resolved."""
    t = topic if (topic and topic in valid_topics) else None
    subs = [s for s in subtopics if s in valid_subtopics] if t else []
    ch = topic_to_chapter.get(t) if t else (chapter if chapter in valid_chapters else None)
    return t, subs, ch


async def _recent_activities(db: AsyncSession, student_id: uuid.UUID, limit: int = 6) -> tuple[str, dict[str, uuid.UUID]]:
    """Return (formatted list for the selector, id_str → entity_id map). Lets the topic
    selector match a question to a SPECIFIC past test without dumping all activity."""
    from app.modules.personalization.models import StudentActivityLog
    rows = (await db.execute(
        select(StudentActivityLog).where(StudentActivityLog.student_id == student_id)
        .order_by(StudentActivityLog.created_at.desc()).limit(limit)
    )).scalars().all()
    by_id: dict[str, uuid.UUID] = {}
    lines: list[str] = []
    for r in rows:
        if not r.entity_id:
            continue
        sid = str(r.entity_id)
        by_id[sid] = r.entity_id
        lines.append(f"{sid} | {r.summary or r.activity_type}")
    return "\n".join(lines), by_id


def _enqueue_chat_summary(student_id: uuid.UUID, session_id: uuid.UUID, history: str, q: str, a: str) -> None:
    """Fire-and-forget the chat-session summary roll-up (best-effort; never blocks the chat)."""
    try:
        from app.core.celery_client import get_celery
        turns = f"{history}\nSTUDENT: {q}\nTUTOR: {a}"[:8000]
        get_celery().send_task(
            "workers.tasks.personalization_tasks.pers_update_chat",
            args=[str(student_id), "tutor", str(session_id), turns], queue="kvi_ai_default",
        )
    except Exception:  # noqa: BLE001
        pass
