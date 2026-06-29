"""Personalization summarization agents (CLAUDE.md §Personalization, spec §4).

All are skill-tunable GPT-5.5 (reasoning) agents that DISTILL stored data into
tutor-ready summaries. They never invent facts — they only condense what they're given.
Output is plain text (+ a few list fields), sized to hold enough useful detail without
becoming raw logs: daily/weekly/chat ≈ 400 words, the extended subjective-mock summary
≈ 800 words (richer mistake history), and the overall intro stays 1–3 sentences. Bilingual
Loksewa context via EXAM_CONTEXT.
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.ai.prompts.shared import EXAM_CONTEXT

logger = logging.getLogger(__name__)

_DAILY_PROMPT = EXAM_CONTEXT + """

ROLE: You maintain ONE rolling daily study summary for a student — a compact picture of
everything they did and asked TODAY (tests attempted, topics, strengths/weaknesses, and the
gist of their tutor chats). A tutor reads it to recall the day without seeing raw detail.

TASK: Merge the PREVIOUS rolling summary with TODAY'S NEW EVENTS into an updated rolling
summary of about 400 words. Retain real specificity (topics, mistake patterns, scores,
notable questions) that the student could later discuss them. Drop stale trivia. Never invent.

--- ADMIN-TUNABLE GUIDANCE (tunes emphasis/level of detail; never invents facts) ---
{skill_instructions}

PREVIOUS ROLLING SUMMARY (may be 'none'):
{previous}

TODAY'S NEW EVENTS (oldest first):
{events}

Return ONLY valid JSON: {{"summary_text": "the updated rolling daily summary"}}"""

_WEEKLY_PROMPT = EXAM_CONTEXT + """

ROLE: You write a student's WEEKLY study summary AND refresh their short overall intro.

TASK: From the week's activity + daily summaries below, produce: (1) a weekly summary of about
400 words covering performance + progress + recurring strengths/weaknesses + the key questions
they kept asking; (2) a refreshed 1–3 sentence overall student intro (their level, strengths,
recurring weak areas, study style). Never invent — base everything on the data.

--- ADMIN-TUNABLE GUIDANCE ---
{skill_instructions}

PREVIOUS INTRO (may be 'none'):
{prev_intro}

THIS WEEK'S ACTIVITY + DAILY SUMMARIES:
{events}

Return ONLY valid JSON:
{{"summary_text": "...", "intro_text": "...", "key_questions": ["...", "..."]}}"""

_CHAT_PROMPT = EXAM_CONTEXT + """

ROLE: You summarize ONE tutor chat session so it can be remembered concisely instead of as raw turns.

TASK: From the conversation turns, write a summary of about 400 words: what the student wanted,
what they struggled with, what was explained, the concepts/examples covered, and any follow-ups
they should revisit. Never invent.

--- ADMIN-TUNABLE GUIDANCE ---
{skill_instructions}

SESSION KIND: {session_kind}

CONVERSATION TURNS (oldest first):
{turns}

Return ONLY valid JSON: {{"summary_text": "the session summary"}}"""

_SUBJ_PROMPT = EXAM_CONTEXT + """

ROLE: You maintain a student's EXTENDED subjective-mock-test summary — a richer rolling picture,
across ALL subjective mock tests, of the KINDS of mistakes they make and what they ask about, so
an evaluator/tutor can guide concrete improvement.

TASK: Merge the PREVIOUS extended summary with the NEW subjective test result + its feedback chat
into an updated summary of about 800 words — this is the RICH, long-horizon record of how this
student writes subjective answers, so keep concrete detail: recurring mistake patterns, which
question types/topics they handle well vs poorly, marks trends, the kinds of things they ask about,
and what concretely improves their answers. Also output a list of recurring mistake KINDS (e.g.
"missing examples", "weak structure", "wrong formula step", "incomplete conclusions"). Never invent.

--- ADMIN-TUNABLE GUIDANCE ---
{skill_instructions}

PREVIOUS EXTENDED SUMMARY (may be 'none'):
{previous}

NEW SUBJECTIVE TEST RESULT + FEEDBACK:
{events}

Return ONLY valid JSON:
{{"summary_text": "...", "mistake_kinds": ["...", "..."]}}"""


async def _skill(db: AsyncSession, agent_type: str) -> str:
    try:
        from app.modules.skill_layer.service import get_active_skill_text
        return await get_active_skill_text(db, agent_type)
    except Exception:
        return ""


def _ctx(db, agent_type, task_type, student_id):
    return {"db": db, "agent_type": agent_type, "task_type": task_type,
            "entity_type": "student", "entity_id": student_id}


class DailySummaryAgent:
    AGENT = "DailySummaryAgent"

    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("reasoning")

    async def summarize(self, *, previous: str, events: str, student_id: uuid.UUID) -> str:
        prompt = _DAILY_PROMPT.format(
            skill_instructions=await _skill(self.db, self.AGENT) or "none",
            previous=(previous or "none")[:4000], events=events[:8000] or "none",
        )
        result = await self.provider.generate_text(prompt, schema={}, audit_ctx=_ctx(self.db, self.AGENT, "daily_summary", student_id))
        return str((result or {}).get("summary_text") or previous or "").strip()


class WeeklySummaryAgent:
    AGENT = "WeeklySummaryAgent"

    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("reasoning")

    async def summarize(self, *, prev_intro: str, events: str, student_id: uuid.UUID) -> dict:
        prompt = _WEEKLY_PROMPT.format(
            skill_instructions=await _skill(self.db, self.AGENT) or "none",
            prev_intro=(prev_intro or "none")[:2000], events=events[:10000] or "none",
        )
        result = await self.provider.generate_text(prompt, schema={}, audit_ctx=_ctx(self.db, self.AGENT, "weekly_summary", student_id)) or {}
        kq = result.get("key_questions")
        return {
            "summary_text": str(result.get("summary_text") or "").strip(),
            "intro_text": str(result.get("intro_text") or prev_intro or "").strip(),
            "key_questions": [str(q) for q in kq][:10] if isinstance(kq, list) else [],
        }


class ChatSessionSummaryAgent:
    AGENT = "ChatSessionSummaryAgent"

    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("reasoning")

    async def summarize(self, *, session_kind: str, turns: str, student_id: uuid.UUID) -> str:
        prompt = _CHAT_PROMPT.format(
            skill_instructions=await _skill(self.db, self.AGENT) or "none",
            session_kind=session_kind, turns=turns[:8000] or "none",
        )
        result = await self.provider.generate_text(prompt, schema={}, audit_ctx=_ctx(self.db, self.AGENT, "chat_session_summary", student_id))
        return str((result or {}).get("summary_text") or "").strip()


class ExtendedSubjectiveSummaryAgent:
    AGENT = "ExtendedSubjectiveSummaryAgent"

    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("reasoning")

    async def summarize(self, *, previous: str, events: str, student_id: uuid.UUID) -> dict:
        prompt = _SUBJ_PROMPT.format(
            skill_instructions=await _skill(self.db, self.AGENT) or "none",
            # ~800-word prior summary ≈ 6k chars — keep enough that the merge doesn't clip it.
            previous=(previous or "none")[:9000], events=events[:8000] or "none",
        )
        result = await self.provider.generate_text(prompt, schema={}, audit_ctx=_ctx(self.db, self.AGENT, "extended_subjective_summary", student_id)) or {}
        mk = result.get("mistake_kinds")
        return {
            "summary_text": str(result.get("summary_text") or previous or "").strip(),
            "mistake_kinds": [str(m) for m in mk][:15] if isinstance(mk, list) else [],
        }
