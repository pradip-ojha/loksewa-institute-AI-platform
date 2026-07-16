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

from sqlalchemy import func, select
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
    # COMMIT (not just flush): the streaming endpoint resolves the session before
    # returning its StreamingResponse, and FastAPI ≥0.106 tears down the get_db
    # dependency (rolling back the open transaction) as soon as the endpoint
    # returns — BEFORE the stream generator runs. A merely-flushed row would be
    # rolled back there, and the turn's final message insert would then violate
    # the session FK. expire_on_commit=False keeps the object usable after commit.
    await db.commit()
    return session


async def get_owned_session(
    db: AsyncSession, student_id: uuid.UUID, session_id: uuid.UUID,
) -> TutorChatSession | None:
    """Return the session ONLY if it exists and belongs to this student, else None.
    Used to resume a chat without the create-on-miss fallback that let a forged
    session_id bypass the enrollment gate."""
    r = await db.execute(
        select(TutorChatSession).where(
            TutorChatSession.id == session_id,
            TutorChatSession.student_id == student_id,
        )
    )
    return r.scalar_one_or_none()


async def list_sessions(db: AsyncSession, student_id: uuid.UUID, exam_id: uuid.UUID) -> list[dict]:
    """The student's tutor sessions for one exam, for the ChatGPT-style sidebar:
    newest activity first, titled by the session's first question. Sessions with
    zero messages (created but never used, e.g. an aborted first turn) are hidden."""
    agg = (
        select(
            TutorChatMessage.session_id,
            func.count().label("message_count"),
            func.max(TutorChatMessage.created_at).label("last_message_at"),
        )
        .group_by(TutorChatMessage.session_id)
        .subquery()
    )
    rows = (await db.execute(
        select(TutorChatSession, agg.c.message_count, agg.c.last_message_at)
        .join(agg, agg.c.session_id == TutorChatSession.id)
        .where(TutorChatSession.student_id == student_id, TutorChatSession.exam_id == exam_id)
        .order_by(agg.c.last_message_at.desc())
    )).all()
    if not rows:
        return []
    ids = [s.id for s, _, _ in rows]
    # First question per session = the sidebar title (PG DISTINCT ON, earliest message wins).
    firsts = (await db.execute(
        select(TutorChatMessage.session_id, TutorChatMessage.question)
        .distinct(TutorChatMessage.session_id)
        .where(TutorChatMessage.session_id.in_(ids))
        .order_by(TutorChatMessage.session_id, TutorChatMessage.created_at.asc())
    )).all()
    title_by = {sid: q for sid, q in firsts}
    return [
        {
            "id": s.id,
            "title": (title_by.get(s.id) or "नयाँ कुराकानी").strip()[:80],
            "message_count": count,
            "created_at": s.created_at,
            "last_message_at": last_at,
        }
        for s, count, last_at in rows
    ]


async def delete_session(db: AsyncSession, student_id: uuid.UUID, session_id: uuid.UUID) -> bool:
    """Delete an owned session (messages cascade via the DB FK). False if not found/owned."""
    session = await get_owned_session(db, student_id, session_id)
    if not session:
        return False
    await db.delete(session)
    await db.commit()
    return True


async def get_history(db: AsyncSession, student_id: uuid.UUID, session_id: uuid.UUID) -> list[TutorChatMessage]:
    r = await db.execute(
        select(TutorChatMessage)
        .where(TutorChatMessage.session_id == session_id, TutorChatMessage.student_id == student_id)
        .order_by(TutorChatMessage.created_at.asc())
    )
    return list(r.scalars().all())


async def get_history_by_exam(
    db: AsyncSession, student_id: uuid.UUID, exam_id: uuid.UUID,
) -> list[TutorChatMessage]:
    """All of this student's tutor turns for the exam, merged across sessions
    (mirrors the video tutor's per-video history) so re-opening the AI Tutor page
    shows the full prior conversation instead of starting blank."""
    r = await db.execute(
        select(TutorChatMessage)
        .join(TutorChatSession, TutorChatSession.id == TutorChatMessage.session_id)
        .where(TutorChatSession.exam_id == exam_id, TutorChatMessage.student_id == student_id)
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


async def _prepare_tutor_turn(
    db: AsyncSession, *, student_id: uuid.UUID, exam_id: uuid.UUID, question: str, session: TutorChatSession,
) -> dict:
    """Shared pre-answer steps for both the sync and streaming tutor chains: topic
    selection (validated against the exam tree), optional activity detail, and the
    exam-filtered notes/book retrieval. Returns everything the answer agent needs."""
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

    return {
        "tree_text": tree_text,
        "history_text": history_text,
        "person_block": "\n\n".join(p for p in [personalization, activity_detail] if p),
        "knowledge_text": knowledge_text,
        "supporting": supporting,
        "detected_topic": detected_topic,
        "detected_subtopics": detected_subtopics,
        "retrieval_query": retrieval_query,
        "selection_confidence": confidence,
    }


async def _persist_tutor_turn(
    db: AsyncSession, *, student_id: uuid.UUID, session: TutorChatSession, question: str,
    prep: dict, answer: str, language: str, confidence: float, follow_ups: list[str],
) -> None:
    msg = TutorChatMessage(
        session_id=session.id,
        student_id=student_id,
        question=question,
        answer=answer,
        language=language,
        detected_topic=prep["detected_topic"],
        detected_subtopic_ids=prep["detected_subtopics"],
        query_rewrite=prep["retrieval_query"],
        supporting_knowledge_json=prep["supporting"],
        confidence=confidence,
        follow_up_suggestions=follow_ups,
    )
    db.add(msg)
    await db.commit()
    # Personalization: roll this turn into the session summary + daily summary (best-effort).
    _enqueue_chat_summary(student_id, session.id, prep["history_text"], question, answer)


async def run_tutor_chain(
    db: AsyncSession, *, student_id: uuid.UUID, exam_id: uuid.UUID, question: str, session: TutorChatSession,
) -> dict:
    """Topic selector → exam-filtered notes/book retrieval → main tutor. Grounding stays
    inside the chosen exam; the selector's topic/subtopics are validated against the
    exam's live syllabus before any retrieval."""
    from app.ai.agents.tutor_agent import TutorAgent

    prep = await _prepare_tutor_turn(db, student_id=student_id, exam_id=exam_id, question=question, session=session)

    # ── Main Tutor answers from the retrieved content + student context ──────────
    answer = await TutorAgent(db).answer(
        question=question, scope="EXAM SYLLABUS:\n" + prep["tree_text"], knowledge_text=prep["knowledge_text"],
        history=prep["history_text"], session_id=session.id, personalization=prep["person_block"],
    )

    await _persist_tutor_turn(
        db, student_id=student_id, session=session, question=question, prep=prep,
        answer=answer["answer"], language=answer["language"],
        confidence=answer["confidence"], follow_ups=answer["follow_up_suggestions"],
    )

    return {
        "answer": answer["answer"],
        "language": answer["language"],
        "chat_session_id": session.id,
        "detected_topic": prep["detected_topic"],
        "detected_subtopic_ids": prep["detected_subtopics"],
        "supporting_knowledge_used": prep["supporting"],
        "confidence": answer["confidence"],
        "selection_confidence": prep["selection_confidence"],
        "follow_up_suggestions": answer["follow_up_suggestions"],
    }


async def run_tutor_chain_stream(
    db: AsyncSession, *, student_id: uuid.UUID, exam_id: uuid.UUID, question: str, session: TutorChatSession,
):
    """Streaming variant of ``run_tutor_chain``. Yields NDJSON-ready event dicts:
    a ``meta`` event (session + detected topic) up front, then ``delta`` events as the
    answer streams, then a ``done`` event with follow-ups once persisted. Any error
    yields an ``error`` event and persists nothing half-written."""
    from app.ai.agents.tutor_agent import TutorAgent

    # First byte immediately: the routing below (topic selector + retrieval) can take
    # tens of seconds of silence, and the frontend aborts the stream after 60s without
    # a chunk — a ping keeps the connection visibly alive before any AI work starts.
    yield {"type": "ping"}

    prep = await _prepare_tutor_turn(db, student_id=student_id, exam_id=exam_id, question=question, session=session)

    yield {
        "type": "meta",
        "chat_session_id": str(session.id),
        "detected_topic": prep["detected_topic"],
        "detected_subtopic_ids": prep["detected_subtopics"],
        "supporting_knowledge_used": prep["supporting"],
        "selection_confidence": prep["selection_confidence"],
    }

    meta_sink: dict = {}
    parts: list[str] = []
    async for delta in TutorAgent(db).answer_stream(
        question=question, scope="EXAM SYLLABUS:\n" + prep["tree_text"], knowledge_text=prep["knowledge_text"],
        history=prep["history_text"], session_id=session.id, personalization=prep["person_block"],
        meta_sink=meta_sink,
    ):
        parts.append(delta)
        yield {"type": "delta", "text": delta}

    answer_text = "".join(parts).strip()
    if not answer_text:
        yield {"type": "error", "message": "tutor returned no answer"}
        return

    follow_ups = meta_sink.get("follow_up_suggestions", [])
    language = meta_sink.get("language", "nepali")
    confidence = meta_sink.get("confidence", 0.0)
    await _persist_tutor_turn(
        db, student_id=student_id, session=session, question=question, prep=prep,
        answer=answer_text, language=language, confidence=confidence, follow_ups=follow_ups,
    )

    yield {
        "type": "done",
        "language": language,
        "confidence": confidence,
        "follow_up_suggestions": follow_ups,
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
