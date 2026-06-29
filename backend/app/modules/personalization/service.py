"""Personalization service (CLAUDE.md §Personalization, spec §4).

Two responsibilities:
  1. UPDATE the global-per-student artifacts (intro, rolling daily summary, weekly
     summary, chat-session summaries, extended subjective summary) on the documented
     triggers. Heavy/AI updates are invoked from Celery tasks so request handlers stay
     fast; everything here is BEST-EFFORT — personalization must never break a core flow.
  2. BUILD compact context strings consumed by the tutors (per spec §4.3).

All keys are `student_id` only — personalization is holistic across all enrolled exams.
"""
import logging
import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.personalization.models import (
    ChatSessionSummary, ExtendedSubjectiveSummary, StudentActivityLog,
    StudentDailySummary, StudentProfile, StudentWeeklySummary,
)

logger = logging.getLogger(__name__)

QA_PER_DAILY_UPDATE = 5  # update the rolling daily summary every 5 new Q-A (spec §4.2)


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


# ── row helpers ─────────────────────────────────────────────────────────────

async def _get_or_create_daily(db: AsyncSession, student_id: uuid.UUID) -> StudentDailySummary:
    row = (await db.execute(select(StudentDailySummary).where(StudentDailySummary.student_id == student_id))).scalar_one_or_none()
    if row is None:
        row = StudentDailySummary(student_id=student_id, summary_text="", summary_date=date.today())
        db.add(row)
        await db.flush()
    return row


async def _get_or_create_profile(db: AsyncSession, student_id: uuid.UUID) -> StudentProfile:
    row = (await db.execute(select(StudentProfile).where(StudentProfile.student_id == student_id))).scalar_one_or_none()
    if row is None:
        row = StudentProfile(student_id=student_id, intro_text="")
        db.add(row)
        await db.flush()
    return row


async def _get_or_create_extended(db: AsyncSession, student_id: uuid.UUID) -> ExtendedSubjectiveSummary:
    row = (await db.execute(select(ExtendedSubjectiveSummary).where(ExtendedSubjectiveSummary.student_id == student_id))).scalar_one_or_none()
    if row is None:
        row = ExtendedSubjectiveSummary(student_id=student_id, summary_text="")
        db.add(row)
        await db.flush()
    return row


# ── Activity logging (fast — no AI) ─────────────────────────────────────────

async def log_activity(
    db: AsyncSession, *, student_id: uuid.UUID, activity_type: str, entity_id: uuid.UUID | None,
    exam_id: uuid.UUID | None, raw_context: dict, summary_line: str,
) -> None:
    """Persist a raw activity record (full detail kept only for the current day).
    Fast + best-effort; the AI roll-up happens later via the personalization tasks."""
    try:
        db.add(StudentActivityLog(
            student_id=student_id, activity_type=activity_type,
            entity_type=activity_type, entity_id=entity_id, exam_id=exam_id,
            activity_date=date.today(), raw_context=raw_context, summary=summary_line[:2000],
        ))
        await db.commit()
    except Exception as exc:
        logger.warning("log_activity failed (continuing): %s", exc)
        await db.rollback()


# ── Rolling daily summary ───────────────────────────────────────────────────

async def _today_events_text(db: AsyncSession, student_id: uuid.UUID) -> str:
    today = date.today()
    logs = (await db.execute(
        select(StudentActivityLog).where(
            StudentActivityLog.student_id == student_id,
            StudentActivityLog.activity_date == today,
        ).order_by(StudentActivityLog.created_at.asc())
    )).scalars().all()
    chats = (await db.execute(
        select(ChatSessionSummary).where(
            ChatSessionSummary.student_id == student_id,
            ChatSessionSummary.updated_at >= datetime.combine(today, datetime.min.time()),
        ).order_by(ChatSessionSummary.updated_at.asc())
    )).scalars().all()

    parts: list[str] = []
    for a in logs:
        parts.append(f"ACTIVITY ({a.activity_type}): {a.summary or (a.raw_context or {})}")
    for c in chats:
        parts.append(f"CHAT ({c.session_kind}): {c.summary_text}")
    return "\n".join(parts)


async def recompute_daily_summary(db: AsyncSession, student_id: uuid.UUID) -> None:
    """Merge today's activities + chat summaries into the ONE rolling daily summary."""
    try:
        from app.ai.agents.personalization_agents import DailySummaryAgent
        events = await _today_events_text(db, student_id)
        if not events.strip():
            return
        row = await _get_or_create_daily(db, student_id)
        # A new day → start the rolling summary fresh.
        previous = row.summary_text if row.summary_date == date.today() else ""
        row.summary_text = await DailySummaryAgent(db).summarize(
            previous=previous, events=events, student_id=student_id,
        )
        row.summary_date = date.today()
        row.qa_since_update = 0
        await db.commit()
    except Exception as exc:
        logger.warning("recompute_daily_summary failed (continuing): %s", exc)
        await db.rollback()


# ── Chat-session summary (+ every-5-Q-A daily roll-up) ──────────────────────

async def summarize_chat_session(
    db: AsyncSession, *, student_id: uuid.UUID, session_kind: str, session_id: uuid.UUID, turns: str,
) -> None:
    """Update the per-session summary (rolling) and, every QA_PER_DAILY_UPDATE turns,
    roll the day's activity into the daily summary."""
    try:
        from app.ai.agents.personalization_agents import ChatSessionSummaryAgent
        summary = await ChatSessionSummaryAgent(db).summarize(
            session_kind=session_kind, turns=turns, student_id=student_id,
        )
        row = (await db.execute(
            select(ChatSessionSummary).where(
                ChatSessionSummary.session_kind == session_kind,
                ChatSessionSummary.session_id == session_id,
            )
        )).scalar_one_or_none()
        if row is None:
            db.add(ChatSessionSummary(
                student_id=student_id, session_kind=session_kind, session_id=session_id,
                summary_text=summary,
            ))
        else:
            row.summary_text = summary
        # Q-A counter on the daily row.
        daily = await _get_or_create_daily(db, student_id)
        daily.qa_since_update = (daily.qa_since_update or 0) + 1
        roll = daily.qa_since_update >= QA_PER_DAILY_UPDATE
        await db.commit()
        if roll:
            await recompute_daily_summary(db, student_id)
    except Exception as exc:
        logger.warning("summarize_chat_session failed (continuing): %s", exc)
        await db.rollback()


# ── Extended subjective summary (after each subjective test) ─────────────────

async def recompute_extended_subjective(db: AsyncSession, student_id: uuid.UUID, new_test_text: str) -> None:
    try:
        from app.ai.agents.personalization_agents import ExtendedSubjectiveSummaryAgent
        row = await _get_or_create_extended(db, student_id)
        result = await ExtendedSubjectiveSummaryAgent(db).summarize(
            previous=row.summary_text, events=new_test_text, student_id=student_id,
        )
        row.summary_text = result["summary_text"]
        row.mistake_kinds = result["mistake_kinds"]
        await db.commit()
    except Exception as exc:
        logger.warning("recompute_extended_subjective failed (continuing): %s", exc)
        await db.rollback()


# ── Beat: nightly compression + weekly summary/intro refresh ────────────────

async def nightly_compress(db: AsyncSession) -> int:
    """Expire raw activity detail for PRIOR days (the rolling daily summary already
    retains the distilled version). Keeps `summary`, clears `raw_context`. No AI."""
    today = date.today()
    rows = (await db.execute(
        select(StudentActivityLog).where(
            StudentActivityLog.distilled.is_(False),
            StudentActivityLog.activity_date < today,
        )
    )).scalars().all()
    n = 0
    for r in rows:
        r.raw_context = None
        r.distilled = True
        n += 1
    if n:
        await db.commit()
    return n


async def _active_student_ids(db: AsyncSession, *, days: int = 7) -> list[uuid.UUID]:
    since = date.today() - timedelta(days=days)
    ids = set((await db.execute(
        select(StudentActivityLog.student_id).where(StudentActivityLog.activity_date >= since)
    )).scalars().all())
    ids.update((await db.execute(
        select(ChatSessionSummary.student_id).where(
            ChatSessionSummary.updated_at >= datetime.combine(since, datetime.min.time())
        )
    )).scalars().all())
    return list(ids)


async def generate_weekly_for_student(db: AsyncSession, student_id: uuid.UUID) -> None:
    """Generate this week's summary + refresh the student intro (spec §4.2)."""
    try:
        from app.ai.agents.personalization_agents import WeeklySummaryAgent
        ws = _week_start(date.today())
        logs = (await db.execute(
            select(StudentActivityLog).where(
                StudentActivityLog.student_id == student_id,
                StudentActivityLog.activity_date >= ws,
            ).order_by(StudentActivityLog.created_at.asc())
        )).scalars().all()
        daily = (await db.execute(
            select(StudentDailySummary).where(StudentDailySummary.student_id == student_id)
        )).scalar_one_or_none()
        events = "\n".join(
            [f"ACTIVITY ({a.activity_type}): {a.summary or ''}" for a in logs]
            + ([f"TODAY'S ROLLING SUMMARY: {daily.summary_text}"] if daily and daily.summary_text else [])
        )
        if not events.strip():
            return
        profile = await _get_or_create_profile(db, student_id)
        result = await WeeklySummaryAgent(db).summarize(
            prev_intro=profile.intro_text, events=events, student_id=student_id,
        )
        # Upsert this week's row.
        existing = (await db.execute(
            select(StudentWeeklySummary).where(
                StudentWeeklySummary.student_id == student_id,
                StudentWeeklySummary.week_start == ws,
            )
        )).scalar_one_or_none()
        if existing is None:
            db.add(StudentWeeklySummary(
                student_id=student_id, week_start=ws,
                summary_text=result["summary_text"], key_questions=result["key_questions"],
            ))
        else:
            existing.summary_text = result["summary_text"]
            existing.key_questions = result["key_questions"]
        profile.intro_text = result["intro_text"]
        await db.commit()
    except Exception as exc:
        logger.warning("generate_weekly_for_student failed (continuing): %s", exc)
        await db.rollback()


async def generate_weekly_all(db: AsyncSession) -> int:
    n = 0
    for sid in await _active_student_ids(db):
        await generate_weekly_for_student(db, sid)
        n += 1
    return n


# ── Context builders (consumed by the tutors — spec §4.3) ───────────────────

async def _latest_weekly(db: AsyncSession, student_id: uuid.UUID) -> str:
    row = (await db.execute(
        select(StudentWeeklySummary).where(StudentWeeklySummary.student_id == student_id)
        .order_by(StudentWeeklySummary.week_start.desc()).limit(1)
    )).scalar_one_or_none()
    return row.summary_text if row else ""


async def _intro(db: AsyncSession, student_id: uuid.UUID) -> str:
    row = (await db.execute(select(StudentProfile).where(StudentProfile.student_id == student_id))).scalar_one_or_none()
    return row.intro_text if row else ""


async def _daily(db: AsyncSession, student_id: uuid.UUID) -> str:
    row = (await db.execute(select(StudentDailySummary).where(StudentDailySummary.student_id == student_id))).scalar_one_or_none()
    return row.summary_text if row else ""


async def build_main_tutor_context(db: AsyncSession, student_id: uuid.UUID) -> str:
    """All normal personalization data for the main AI tutor."""
    try:
        intro = await _intro(db, student_id)
        daily = await _daily(db, student_id)
        weekly = await _latest_weekly(db, student_id)
        recent_chats = (await db.execute(
            select(ChatSessionSummary).where(ChatSessionSummary.student_id == student_id)
            .order_by(ChatSessionSummary.updated_at.desc()).limit(3)
        )).scalars().all()
        parts = []
        if intro:
            parts.append(f"STUDENT INTRO: {intro}")
        if weekly:
            parts.append(f"THIS WEEK: {weekly}")
        if daily:
            parts.append(f"TODAY SO FAR: {daily}")
        if recent_chats:
            parts.append("RECENT CHATS: " + " | ".join(c.summary_text for c in recent_chats if c.summary_text))
        return "\n".join(parts)
    except Exception as exc:
        logger.warning("build_main_tutor_context failed: %s", exc)
        return ""


async def build_subjective_feedback_context(db: AsyncSession, student_id: uuid.UUID) -> str:
    """Extended subjective summary + intro + weekly (the caller adds the session +
    last 5 chat turns from the feedback chat itself)."""
    try:
        ext = (await db.execute(
            select(ExtendedSubjectiveSummary).where(ExtendedSubjectiveSummary.student_id == student_id)
        )).scalar_one_or_none()
        intro = await _intro(db, student_id)
        weekly = await _latest_weekly(db, student_id)
        parts = []
        if intro:
            parts.append(f"STUDENT INTRO: {intro}")
        if weekly:
            parts.append(f"THIS WEEK: {weekly}")
        if ext and ext.summary_text:
            parts.append(f"SUBJECTIVE MOCK HISTORY: {ext.summary_text}")
            if ext.mistake_kinds:
                parts.append("RECURRING MISTAKE KINDS: " + ", ".join(ext.mistake_kinds))
        return "\n".join(parts)
    except Exception as exc:
        logger.warning("build_subjective_feedback_context failed: %s", exc)
        return ""


async def build_video_tutor_context(db: AsyncSession, student_id: uuid.UUID) -> str:
    """Student intro + weekly summary (the caller adds this video's own chat session)."""
    try:
        intro = await _intro(db, student_id)
        weekly = await _latest_weekly(db, student_id)
        parts = []
        if intro:
            parts.append(f"STUDENT INTRO: {intro}")
        if weekly:
            parts.append(f"THIS WEEK: {weekly}")
        return "\n".join(parts)
    except Exception as exc:
        logger.warning("build_video_tutor_context failed: %s", exc)
        return ""


async def get_activity_detail(db: AsyncSession, student_id: uuid.UUID, entity_id: uuid.UUID) -> str:
    """Activity-aware retrieval (spec §4.4): the detail of ONE past activity the tutor's
    topic selector flagged. Uses the raw context when still present (current day),
    otherwise the distilled summary line."""
    try:
        row = (await db.execute(
            select(StudentActivityLog).where(
                StudentActivityLog.student_id == student_id,
                StudentActivityLog.entity_id == entity_id,
            ).order_by(StudentActivityLog.created_at.desc()).limit(1)
        )).scalar_one_or_none()
        if not row:
            return ""
        if row.raw_context:
            return f"PAST ACTIVITY DETAIL ({row.activity_type}): {row.raw_context}"
        return f"PAST ACTIVITY ({row.activity_type}): {row.summary or ''}"
    except Exception as exc:
        logger.warning("get_activity_detail failed: %s", exc)
        return ""
