"""Video Tutor service: DB queries, signed-URL helpers, syllabus-tree resolution,
and the timeline-first Q&A chain.

The heavy processing pipeline lives in the Celery task
(`workers/tasks/video_tasks.py`) which calls the agents in `app/ai/agents/`. The Q&A
chain (`run_qa_chain`) is synchronous (a fast multi-agent chat call), so it lives here
and is invoked directly from the router.

Architecture rule (CLAUDE.md §13): Q&A is timeline-first. The full lecture summary is
ALWAYS passed to the tutor; segment routing uses timeline labels/descriptions; knowledge
retrieval is filtered to the routed topic/subtopic — never a global transcript vector search.
"""
import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.r2_client import get_r2
from app.modules.files.models import File
from app.modules.syllabus.models import SyllabusItem
from app.modules.video.models import (
    Video, VideoChatMessage, VideoChatSession, VideoSlideLabel,
    VideoSummary, VideoTimelineSegment, VideoView,
)

logger = logging.getLogger(__name__)

_VIDEO_TERMINAL = ("completed", "failed")


async def fail_orphaned_videos(db: AsyncSession) -> int:
    """Mark in-progress videos as failed when their processing job is gone.

    Backstop for a worker that died mid-job: the job row is failed by the reaper /
    startup recovery, but the video's own `processing_status` stays at
    'transcribing'/'generating_timeline'/… forever, so the admin Library spins with no
    path back to retry. Any video not in a terminal status whose `processing_job_id` is
    missing or points at a failed/cancelled job becomes 'failed' (which enables retry).
    Returns how many were reconciled."""
    from app.modules.jobs.models import JobStatus, ProcessingJob

    dead = {JobStatus.failed, JobStatus.cancelled}
    reconciled = 0
    videos = (await db.execute(
        select(Video).where(~Video.processing_status.in_(_VIDEO_TERMINAL))
    )).scalars().all()
    for v in videos:
        job = None
        if v.processing_job_id:
            job = (await db.execute(
                select(ProcessingJob).where(ProcessingJob.id == v.processing_job_id)
            )).scalar_one_or_none()
        if job is None or job.status in dead:
            v.processing_status = "failed"
            reconciled += 1
    if reconciled:
        await db.commit()
    return reconciled


# ── Time helpers ─────────────────────────────────────────────────────────────────

def fmt_timestamp(seconds: float | int | None) -> str:
    s = int(max(0, float(seconds or 0)))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


def parse_timestamp(value: str | None) -> float | None:
    """Accept 'HH:MM:SS', 'MM:SS', or a plain seconds string. Returns seconds or None."""
    if not value:
        return None
    value = str(value).strip()
    try:
        if ":" in value:
            parts = [float(p) for p in value.split(":")]
            sec = 0.0
            for p in parts:
                sec = sec * 60 + p
            return sec
        return float(value)
    except (TypeError, ValueError):
        return None


def _seg_id(index: int) -> str:
    return f"seg_{index + 1:03d}"


# ── Signed URLs ──────────────────────────────────────────────────────────────────

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


# ── Admin: videos ────────────────────────────────────────────────────────────────

async def get_video(db: AsyncSession, video_id: uuid.UUID) -> Video | None:
    r = await db.execute(select(Video).where(Video.id == video_id))
    return r.scalar_one_or_none()


async def list_videos(db: AsyncSession, *, page: int = 1, per_page: int = 20):
    total = (await db.execute(select(func.count()).select_from(Video))).scalar_one()
    r = await db.execute(
        select(Video).order_by(Video.created_at.desc())
        .offset((page - 1) * per_page).limit(per_page)
    )
    return r.scalars().all(), total


async def set_status(db: AsyncSession, video_id: uuid.UUID, status: str) -> Video | None:
    v = await get_video(db, video_id)
    if not v:
        return None
    v.status = status
    await db.commit()
    await db.refresh(v)
    return v


async def delete_video(db: AsyncSession, video_id: uuid.UUID) -> bool:
    v = await get_video(db, video_id)
    if not v:
        return False
    await db.delete(v)
    await db.commit()
    return True


def video_out_fields(v: Video) -> dict:
    return {
        "id": v.id,
        "display_name": v.display_name,
        "exam_id": v.exam_id,
        "topic": v.topic,
        "subtopic": v.subtopic,
        "processing_status": v.processing_status,
        "status": v.status,
        "duration_seconds": v.duration_seconds,
        "is_audio_only": v.is_audio_only,
        "has_slides": v.support_slides_file_id is not None,
        "processing_job_id": v.processing_job_id,
        "created_at": v.created_at,
    }


# ── Timeline / summary / slides ──────────────────────────────────────────────────

async def get_timeline(db: AsyncSession, video_id: uuid.UUID) -> list[VideoTimelineSegment]:
    r = await db.execute(
        select(VideoTimelineSegment)
        .where(VideoTimelineSegment.video_id == video_id)
        .order_by(VideoTimelineSegment.segment_index)
    )
    return list(r.scalars().all())


async def get_summary(db: AsyncSession, video_id: uuid.UUID) -> VideoSummary | None:
    r = await db.execute(
        select(VideoSummary).where(VideoSummary.video_id == video_id)
        .order_by(VideoSummary.created_at.desc()).limit(1)
    )
    return r.scalar_one_or_none()


async def get_slides(db: AsyncSession, video_id: uuid.UUID) -> list[VideoSlideLabel]:
    r = await db.execute(
        select(VideoSlideLabel).where(VideoSlideLabel.video_id == video_id)
        .order_by(VideoSlideLabel.slide_number)
    )
    return list(r.scalars().all())


def timeline_out(seg: VideoTimelineSegment) -> dict:
    return {
        "segment_id": _seg_id(seg.segment_index),
        "segment_index": seg.segment_index,
        "start_seconds": seg.start_seconds,
        "end_seconds": seg.end_seconds,
        "start_time": fmt_timestamp(seg.start_seconds),
        "end_time": fmt_timestamp(seg.end_seconds),
        "label": seg.label,
        "description": seg.description,
        "summary": seg.summary,
        "topic": seg.topic,
        "subtopic_ids": seg.subtopic_ids or [],
        "mapping_confidence": seg.mapping_confidence,
    }


def summary_out(s: VideoSummary | None) -> dict | None:
    if not s:
        return None
    return {
        "short_summary": s.short_summary,
        "detailed_summary": s.detailed_summary,
        "key_points": s.key_points or [],
        "exam_focused_points": s.exam_focused_points or [],
        "important_terms": s.important_terms or [],
        "possible_questions": s.possible_questions or {},
    }


def slide_out(s: VideoSlideLabel) -> dict:
    return {
        "slide_number": s.slide_number,
        "slide_id": s.slide_id,
        "title": s.title,
        "related_timestamps": s.related_timestamps or [],
        "topics": s.topics or [],
        "summary": s.summary,
    }


# ── Student ──────────────────────────────────────────────────────────────────────

async def list_student_videos(
    db: AsyncSession, student_id: uuid.UUID, exam_id: uuid.UUID | None = None,
) -> list[Video]:
    """``exam_id`` narrows to the student's currently-selected exam (shared exam
    picker across MCQ/Video/Subjective/AI Tutor)."""
    from app.modules.exams.service import get_enrolled_exam_ids
    enrolled = await get_enrolled_exam_ids(db, student_id)
    if not enrolled:
        return []
    exam_filter = [exam_id] if exam_id is not None else enrolled
    r = await db.execute(
        select(Video).where(
            Video.status == "active", Video.processing_status == "completed",
            Video.exam_id.in_(enrolled), Video.exam_id.in_(exam_filter),
        )
        .order_by(Video.created_at.desc())
    )
    return list(r.scalars().all())


async def record_view(db: AsyncSession, video_id: uuid.UUID, student_id: uuid.UUID) -> None:
    db.add(VideoView(video_id=video_id, student_id=student_id))
    await db.commit()


async def get_or_create_chat_session(
    db: AsyncSession, video_id: uuid.UUID, student_id: uuid.UUID, session_id: uuid.UUID | None,
) -> VideoChatSession:
    if session_id:
        r = await db.execute(
            select(VideoChatSession).where(
                VideoChatSession.id == session_id,
                VideoChatSession.student_id == student_id,
                VideoChatSession.video_id == video_id,
            )
        )
        existing = r.scalar_one_or_none()
        if existing:
            return existing
    session = VideoChatSession(video_id=video_id, student_id=student_id)
    db.add(session)
    # COMMIT (not just flush) — same reason as tutor get_or_create_session: the
    # stream endpoint resolves the session before returning its StreamingResponse,
    # and get_db teardown rolls back a merely-flushed row before the generator
    # persists the turn (FK violation on video_chat_messages otherwise).
    await db.commit()
    return session


async def get_chat_history(db: AsyncSession, video_id: uuid.UUID, student_id: uuid.UUID) -> list[VideoChatMessage]:
    r = await db.execute(
        select(VideoChatMessage)
        .where(VideoChatMessage.video_id == video_id, VideoChatMessage.student_id == student_id)
        .order_by(VideoChatMessage.created_at.asc())
    )
    return list(r.scalars().all())


# In-session conversational memory for the answer agent (mirrors the main tutor's
# _recent_history_text) so follow-ups like "explain that again" have prior-turn context.
MAX_VIDEO_CHAT_HISTORY = 6


async def _recent_history_text(db: AsyncSession, session_id: uuid.UUID) -> str:
    r = await db.execute(
        select(VideoChatMessage)
        .where(VideoChatMessage.session_id == session_id)
        .order_by(VideoChatMessage.created_at.desc())
        .limit(MAX_VIDEO_CHAT_HISTORY)
    )
    msgs = list(r.scalars().all())[::-1]
    parts: list[str] = []
    for m in msgs:
        parts.append(f"STUDENT: {m.question}")
        if m.answer:
            parts.append(f"TUTOR: {m.answer}")
    return "\n".join(parts)


# ── Syllabus tree (per exam) ──────────────────────────────────────────────────

async def get_chapter_tree(
    db: AsyncSession, exam_id: uuid.UUID, chapter: str | None = None
) -> tuple[str, set[str], set[str], set[str], dict[str, str]]:
    """Return (tree_text, valid_topics, valid_subtopics, valid_chapters, topic_to_chapter).

    Scoped by `exam_id` (replacing the old objective/subjective `content_usage_type`).
    The tree text is what the routing/mapping agents are constrained to choose from, and
    now leads with CHAPTER — the PRIMARY retrieval dimension (CLAUDE.md §8) — so agents can
    return the chapter a topic belongs to. `topic_to_chapter` is the deterministic
    topic→chapter lookup (each topic belongs to one chapter; on the rare collision the
    agent's explicit chapter disambiguates). Shared by the video Q&A chain, the main AI
    tutor, and subjective skill generation.

    Pass `chapter` to scope the whole result to a single chapter (the knowledge layer's
    "locked-chapter" ingest mode, CLAUDE.md §8): every returned structure then covers only
    that chapter's topics/subtopics. Default `None` = the full exam tree (all callers that
    omit it are unchanged).
    """
    r = await db.execute(
        select(SyllabusItem)
        .where(SyllabusItem.exam_id == exam_id, SyllabusItem.is_active.is_(True))
        .order_by(SyllabusItem.sort_order)
    )
    items = list(r.scalars().all())
    if chapter:
        items = [it for it in items if (it.chapter or "(unspecified)") == chapter]

    valid_topics: set[str] = set()
    valid_subtopics: set[str] = set()
    valid_chapters: set[str] = set()
    topic_to_chapter: dict[str, str] = {}
    # chapter -> {topic -> [subtopics]}
    by_chapter: dict[str, dict[str, list[str]]] = {}
    for it in items:
        chapter = it.chapter or "(unspecified)"
        valid_chapters.add(chapter)
        by_chapter.setdefault(chapter, {})
        if it.topic:
            valid_topics.add(it.topic)
            topic_to_chapter.setdefault(it.topic, chapter)
            by_chapter[chapter].setdefault(it.topic, [])
            if it.subtopic:
                valid_subtopics.add(it.subtopic)
                by_chapter[chapter][it.topic].append(it.subtopic)

    lines: list[str] = []
    for chapter, topics in by_chapter.items():
        lines.append(f"- CHAPTER: {chapter}")
        for topic, subs in topics.items():
            lines.append(f"    - TOPIC: {topic}")
            for sub in subs:
                lines.append(f"        - SUBTOPIC: {sub}")
    tree_text = "\n".join(lines) or "(no syllabus topics configured)"
    return tree_text, valid_topics, valid_subtopics, valid_chapters, topic_to_chapter


def resolve_syllabus_labels(
    *,
    topic: str | None,
    subtopic: str | None,
    chapter: str | None,
    valid_topics: set[str],
    valid_subtopics: set[str],
    valid_chapters: set[str],
    topic_to_chapter: dict[str, str],
    locked_chapter: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Validate a single item's (topic, subtopic, chapter) against the exam syllabus and
    return official-or-None labels. CHAPTER (the PRIMARY retrieval dimension) is resolved
    deterministically from the validated topic via `topic_to_chapter`; the caller's explicit
    chapter is used only when no topic resolved. Mirrors `tutor/service.py::_validate` for a
    single (not plural) subtopic — used by the knowledge chunk→syllabus mapper (CLAUDE.md §8).

    `locked_chapter` forces the chapter to a single admin-chosen value (the knowledge layer's
    single-chapter ingest mode): the chapter is always that value, only topic/subtopic vary
    within it. The literal "(unspecified)" chapter (rows with no chapter) is treated as None.
    """
    t = topic if (topic and topic in valid_topics) else None
    sub = subtopic if (t and subtopic and subtopic in valid_subtopics) else None
    if locked_chapter:
        ch: str | None = locked_chapter
    else:
        ch = topic_to_chapter.get(t) if t else None
        if not ch and chapter and chapter in valid_chapters:
            ch = chapter
    if ch == "(unspecified)":
        ch = None
    return t, sub, ch


# ── Q&A chain (timeline-first) ───────────────────────────────────────────────────

async def _prepare_qa_turn(
    db: AsyncSession, *, video: Video, question: str,
    current_video_time: str | None, session: VideoChatSession,
) -> dict:
    """Shared pre-answer steps for both the sync and streaming Q&A chains: segment
    router → topic/subtopic router → conditional knowledge retrieval → personalization.
    Returns everything the answer agent + persistence need."""
    from app.ai.agents.video_segment_router_agent import VideoSegmentRouterAgent
    from app.ai.agents.video_topic_router_agent import VideoTopicRouterAgent

    segments = await get_timeline(db, video.id)
    summary = await get_summary(db, video.id)
    lecture_summary_text = ""
    if summary:
        lecture_summary_text = (summary.detailed_summary or summary.short_summary or "")

    seg_by_id = {_seg_id(s.segment_index): s for s in segments}

    # ── Step 1: Segment Router ───────────────────────────────────────────────────
    selected: list[VideoTimelineSegment] = []
    if segments:
        segments_block = "\n".join(
            f"{_seg_id(s.segment_index)} | {fmt_timestamp(s.start_seconds)}-{fmt_timestamp(s.end_seconds)} "
            f"| {s.label} | {s.description}"
            for s in segments
        )
        try:
            routed = await VideoSegmentRouterAgent(db).route(
                question=question, current_time=current_video_time,
                segments_block=segments_block, video_id=video.id,
            )
            selected = [seg_by_id[sid] for sid in routed.get("selected_segment_ids", []) if sid in seg_by_id]
        except Exception as exc:
            logger.warning("segment routing failed, using fallback: %s", exc)

        if not selected:
            selected = _fallback_segments(segments, current_video_time)

    # ── Step 2: Fetch selected segment content ───────────────────────────────────
    segment_context = _format_segment_context(selected, include_transcript=False)
    segment_content = _format_segment_context(selected, include_transcript=True)

    # ── Step 3: Topic/Subtopic Router (also flags whether the knowledge layer is needed) ─
    tree_text, valid_topics, valid_subtopics, _valid_chapters, _topic_to_chapter = await get_chapter_tree(db, video.exam_id)
    detected_topic: str | None = None
    detected_subtopics: list[str] = []
    needs_knowledge = False
    if valid_topics:
        try:
            topic_route = await VideoTopicRouterAgent(db).route(
                question=question, lecture_summary=lecture_summary_text,
                segment_context=segment_context, tree_text=tree_text, video_id=video.id,
            )
            cand_topic = topic_route.get("topic")
            if cand_topic in valid_topics:
                detected_topic = cand_topic
            detected_subtopics = [s for s in topic_route.get("subtopic_ids", []) if s in valid_subtopics]
            needs_knowledge = bool(topic_route.get("needs_knowledge"))
        except Exception as exc:
            logger.warning("topic routing failed: %s", exc)
    # Fall back to the segments' own mapped topic when the router is unsure.
    if not detected_topic:
        for s in selected:
            if s.topic and s.topic in valid_topics:
                detected_topic = s.topic
                break

    # ── Step 4: Fetch supporting knowledge ONLY when the question is deep enough ──
    # (spec §5.3 — the lecture transcript/summary answers most questions; reach for the
    # book/notes layer only when the topic router flags it).
    knowledge_text, supporting = "", []
    if needs_knowledge:
        knowledge_text, supporting = await fetch_supporting_knowledge(
            db, exam_id=video.exam_id, chapter=video.chapter,
            topic=detected_topic, subtopic_ids=detected_subtopics, question=question,
        )

    # ── Step 5 prefix: personalization + in-session history (the answer itself runs in the caller) ─
    from app.modules.personalization import service as pers
    personalization = await pers.build_video_tutor_context(db, session.student_id)
    history_text = await _recent_history_text(db, session.id)

    selected_out = [{
        "segment_id": _seg_id(s.segment_index),
        "label": s.label,
        "start_time": fmt_timestamp(s.start_seconds),
        "end_time": fmt_timestamp(s.end_seconds),
        "start_seconds": s.start_seconds,
    } for s in selected]

    return {
        "lecture_summary_text": lecture_summary_text,
        "segment_content": segment_content or "(no specific lecture segment matched this question)",
        "knowledge_text": knowledge_text,
        "supporting": supporting,
        "detected_topic": detected_topic,
        "detected_subtopics": detected_subtopics,
        "selected_out": selected_out,
        "personalization": personalization,
        "history_text": history_text,
    }


async def _persist_qa_turn(
    db: AsyncSession, *, video: Video, session: VideoChatSession, question: str, prep: dict,
    answer: str, language: str, confidence: float, follow_ups: list[str],
) -> None:
    selected_out = prep["selected_out"]
    msg = VideoChatMessage(
        session_id=session.id,
        video_id=video.id,
        student_id=session.student_id,
        question=question,
        answer=answer,
        language=language,
        selected_segment_ids=[s["segment_id"] for s in selected_out],
        detected_topic=prep["detected_topic"],
        detected_subtopic_ids=prep["detected_subtopics"],
        sources_json={"selected_segments": selected_out},
        supporting_knowledge_json=prep["supporting"],
        confidence=confidence,
        follow_up_suggestions=follow_ups,
    )
    db.add(msg)
    await db.commit()

    # Personalization: roll this turn into the video chat-session summary (best-effort).
    try:
        from app.core.celery_client import get_celery
        get_celery().send_task(
            "workers.tasks.personalization_tasks.pers_update_chat",
            args=[str(session.student_id), "video", str(session.id),
                  f"STUDENT: {question}\nTUTOR: {answer}"[:8000]],
            queue="kvi_ai_default",
        )
    except Exception:  # noqa: BLE001
        pass


async def run_qa_chain(
    db: AsyncSession, *, video: Video, question: str,
    current_video_time: str | None, session: VideoChatSession,
) -> dict:
    """Full timeline-first Q&A: segment router → topic/subtopic router →
    filtered knowledge retrieval → main tutor. The full lecture summary is always
    included for global context."""
    from app.ai.agents.video_tutor_agent import VideoTutorAgent

    prep = await _prepare_qa_turn(db, video=video, question=question, current_video_time=current_video_time, session=session)

    answer = await VideoTutorAgent(db).answer(
        question=question, lecture_summary=prep["lecture_summary_text"],
        segment_content=prep["segment_content"], knowledge_text=prep["knowledge_text"],
        video_id=video.id, personalization=prep["personalization"], history=prep["history_text"],
    )

    await _persist_qa_turn(
        db, video=video, session=session, question=question, prep=prep,
        answer=answer["answer"], language=answer["language"],
        confidence=answer["confidence"], follow_ups=answer["follow_up_suggestions"],
    )

    return {
        "answer": answer["answer"],
        "language": answer["language"],
        "chat_session_id": session.id,
        "selected_segments": prep["selected_out"],
        "detected_topic": prep["detected_topic"],
        "detected_subtopic_ids": prep["detected_subtopics"],
        "supporting_knowledge_used": prep["supporting"],
        "confidence": answer["confidence"],
        "follow_up_suggestions": answer["follow_up_suggestions"],
    }


async def run_qa_chain_stream(
    db: AsyncSession, *, video: Video, question: str,
    current_video_time: str | None, session: VideoChatSession,
):
    """Streaming variant of ``run_qa_chain``. Yields a ``meta`` event (session +
    selected segments + detected topic), then ``delta`` events as the answer streams,
    then a ``done`` event with follow-ups once persisted. Errors yield an ``error`` event."""
    from app.ai.agents.video_tutor_agent import VideoTutorAgent

    # First byte immediately: segment/topic routing below runs two model calls before
    # the meta event, and the frontend aborts the stream after 60s of silence.
    yield {"type": "ping"}

    prep = await _prepare_qa_turn(db, video=video, question=question, current_video_time=current_video_time, session=session)

    yield {
        "type": "meta",
        "chat_session_id": str(session.id),
        "selected_segments": prep["selected_out"],
        "detected_topic": prep["detected_topic"],
        "detected_subtopic_ids": prep["detected_subtopics"],
        "supporting_knowledge_used": prep["supporting"],
    }

    meta_sink: dict = {}
    parts: list[str] = []
    async for delta in VideoTutorAgent(db).answer_stream(
        question=question, lecture_summary=prep["lecture_summary_text"],
        segment_content=prep["segment_content"], knowledge_text=prep["knowledge_text"],
        video_id=video.id, personalization=prep["personalization"], history=prep["history_text"],
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
    await _persist_qa_turn(
        db, video=video, session=session, question=question, prep=prep,
        answer=answer_text, language=language, confidence=confidence, follow_ups=follow_ups,
    )

    yield {
        "type": "done",
        "language": language,
        "confidence": confidence,
        "follow_up_suggestions": follow_ups,
    }


def _fallback_segments(segments: list[VideoTimelineSegment], current_time: str | None) -> list[VideoTimelineSegment]:
    """Low-confidence fallback: prefer the segment containing the current video time,
    else the first segment. Returns at most 2 nearby segments."""
    t = parse_timestamp(current_time)
    if t is not None:
        for i, s in enumerate(segments):
            if s.start_seconds <= t <= s.end_seconds:
                return segments[i:i + 2]
        # nearest by start time
        nearest = min(range(len(segments)), key=lambda i: abs(segments[i].start_seconds - t))
        return segments[nearest:nearest + 2]
    return segments[:1]


def _format_segment_context(segments: list[VideoTimelineSegment], *, include_transcript: bool) -> str:
    parts: list[str] = []
    for s in segments:
        block = (
            f"━━━ {_seg_id(s.segment_index)} ({fmt_timestamp(s.start_seconds)}–{fmt_timestamp(s.end_seconds)}) ━━━\n"
            f"LABEL: {s.label}\n"
            f"DESCRIPTION: {s.description}\n"
            f"SUMMARY: {s.summary}"
        )
        if include_transcript and s.original_transcript:
            block += f"\nORIGINAL TRANSCRIPT:\n{s.original_transcript}"
        parts.append(block)
    return "\n\n".join(parts)


async def fetch_supporting_knowledge(
    db: AsyncSession, *, exam_id: uuid.UUID, topic: str | None,
    subtopic_ids: list[str], question: str, chapter: str | None = None, top_k: int = 5,
) -> tuple[str, list[dict]]:
    """Vector search ONLY inside the routed knowledge set (never global), ALWAYS filtered
    by `exam_id` so retrieval never crosses exams, and by `chapter` (the PRIMARY retrieval
    dimension, CLAUDE.md §8) when known so it never crosses chapters within an exam;
    topic/subtopic narrow within the chapter.
    Runs a dual (prose + model_qa) query so model-answer pairs are retrieved alongside prose
    (CLAUDE.md §8). Returns (knowledge_text, supporting_list). Best-effort: returns empty on any
    failure so Q&A still works grounded in the lecture alone."""
    try:
        from app.ai.model_router import get_provider
        from app.modules.knowledge.retrieval import query_knowledge_dual

        embeddings = await get_provider("reasoning").embed([question])
        if not embeddings:
            return "", []
        filter_dict: dict = {"exam_id": str(exam_id)}
        if chapter:
            filter_dict["chapter"] = chapter
        if topic:
            filter_dict["topic"] = topic
        if subtopic_ids:
            filter_dict["subtopic"] = {"$in": subtopic_ids}

        hits = await query_knowledge_dual(
            db, embedding=embeddings[0], base_filter=filter_dict, prose_top_k=top_k,
        )
        if not hits:
            return "", []

        lines: list[str] = []
        supporting: list[dict] = []
        for h in hits:
            if h.is_qa:
                lines.append(f"[Model Q&A — प्रश्न: {h.question or 'General'}]\n{h.content}")
                supporting.append(
                    {"chunk_id": h.chunk_id, "topic": h.topic, "subtopic": h.subtopic, "question": h.question}
                )
            else:
                label = " | ".join(filter(None, [h.topic, h.subtopic])) or "General"
                lines.append(f"[{label}]\n{h.content}")
                supporting.append({"chunk_id": h.chunk_id, "topic": h.topic, "subtopic": h.subtopic})
        return "\n\n".join(lines), supporting
    except Exception as exc:
        logger.warning("supporting-knowledge retrieval failed (continuing lecture-only): %s", exc)
        return "", []
