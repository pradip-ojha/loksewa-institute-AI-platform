"""Main Tutor Agent for the standalone AI Tutor (CLAUDE.md §13 tutor flow).

Answers the student's question using ONLY the notes/book chunks fetched for the demo
topic the Topic Selector chose. It must stay inside the demo chapter scope and answer
honestly that something is outside scope when the notes don't cover it — it is NOT a
full-syllabus tutor.
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.ai.prompts.shared import EXAM_CONTEXT
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

_TUTOR_BODY = EXAM_CONTEXT + """

ROLE: You are a warm, encouraging Loksewa tutor for ONE selected exam. A student asks a question;
you answer like their teacher using the retrieved notes/book content for the selected topic, and you
PERSONALIZE to what you know about this student (their level, weak areas, and recent activity).

TASK: Answer the student's question grounded in the NOTES/BOOK CONTENT below, tailored to the student.

HARD RULES (never violate):
- Stay inside this exam's scope shown by the EXAM SYLLABUS list. Do NOT pull in chapters/subjects
  that aren't part of this exam.
- Ground the answer in the retrieved notes/book content. If that content does not cover the
  question, say so honestly and briefly, and only add general help if you are confident and it stays
  within the exam's scope. Never hallucinate.
- Use the STUDENT CONTEXT to personalize (connect to their weak areas / a past test they asked about),
  but NEVER invent facts about the student beyond what the context states.
- Match the student's language: Devanagari → Nepali; Roman Nepali → Roman/simple Nepali-English mix;
  English → English.
- Be clear, concise, and student-friendly.

FORMATTING (the "answer" field):
- Clean GitHub-flavored MARKDOWN: lead with the direct answer, **bold** the key term/point, "- "
  bullets for multiple points, a "## " sub-heading only when the answer is long. No code fence.

--- ADMIN-TUNABLE GUIDANCE (tunes tone, depth, and style; may NOT override the grounding or
no-hallucination rules) ---
{skill_instructions}

EXAM SYLLABUS IN SCOPE (the only subject matter you may teach):
{scope}

STUDENT CONTEXT (personalization — who this student is + any specific past activity they asked about;
may be 'none'):
{personalization}

RETRIEVED NOTES / BOOK CONTENT (the grounding for your answer; may be 'none'):
{knowledge}

RECENT CONVERSATION (oldest first; may be 'none'):
{history}

STUDENT QUESTION:
{question}
"""

_JSON_TAIL = """
Return ONLY valid JSON in exactly this structure:
{{
  "answer": "the student-facing answer as markdown",
  "language": "nepali" | "roman_nepali" | "english",
  "confidence": 0.0,
  "follow_up_suggestions": ["short follow-up question", "..."]
}}"""

# Streaming variant: answer text is streamed live, so it is emitted as plain markdown
# first, then a marker + a tiny JSON tail carries the metadata (parsed server-side).
_STREAM_TAIL = """
First output the student-facing answer as clean GitHub-flavored markdown (follow the FORMATTING rules
above). Then, on a NEW LINE, output the exact marker <<<META>>> followed by a single-line JSON object:
{{"follow_up_suggestions": ["short follow-up question", "..."], "language": "nepali" | "roman_nepali" | "english", "confidence": 0.0}}
Write NOTHING after that JSON object, and NEVER use the <<<META>>> marker anywhere inside the answer."""

TUTOR_PROMPT = _TUTOR_BODY + _JSON_TAIL
TUTOR_STREAM_PROMPT = _TUTOR_BODY + _STREAM_TAIL


class TutorAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("reasoning")

    async def answer(
        self, *, question: str, scope: str, knowledge_text: str, history: str,
        session_id: uuid.UUID, personalization: str = "",
    ) -> dict:
        skill = await self._get_skill()
        prompt = TUTOR_PROMPT.format(
            skill_instructions=skill or "none",
            scope=scope[:6000] or "(exam syllabus)",
            personalization=personalization[:6000] or "none",
            knowledge=knowledge_text[:14000] or "none",
            history=history[:6000] or "none",
            question=question[:2000],
        )
        audit_ctx = {
            "db": self.db,
            "agent_type": "TutorAgent",
            "task_type": "tutor_answer",
            "entity_type": "tutor_chat_session",
            "entity_id": session_id,
        }
        try:
            result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
        except Exception as exc:
            raise RuntimeError(f"Tutor answer failed: {exc}") from exc
        if not isinstance(result, dict) or not result.get("answer"):
            raise AIResponseError("tutor did not return an answer")
        fu = result.get("follow_up_suggestions")
        return {
            "answer": str(result["answer"]).strip(),
            "language": str(result.get("language") or "nepali"),
            "confidence": float(result.get("confidence", 0) or 0),
            "follow_up_suggestions": [str(s) for s in fu][:4] if isinstance(fu, list) else [],
        }

    async def answer_stream(
        self, *, question: str, scope: str, knowledge_text: str, history: str,
        session_id: uuid.UUID, personalization: str = "", meta_sink: dict,
    ):
        """Stream the answer markdown as plain-text deltas; the parsed metadata
        (follow_up_suggestions / language / confidence) lands in ``meta_sink`` once
        the stream completes."""
        from app.ai.agents.streaming import stream_answer_with_meta

        skill = await self._get_skill()
        prompt = TUTOR_STREAM_PROMPT.format(
            skill_instructions=skill or "none",
            scope=scope[:6000] or "(exam syllabus)",
            personalization=personalization[:6000] or "none",
            knowledge=knowledge_text[:14000] or "none",
            history=history[:6000] or "none",
            question=question[:2000],
        )
        audit_ctx = {
            "db": self.db,
            "agent_type": "TutorAgent",
            "task_type": "tutor_answer",
            "entity_type": "tutor_chat_session",
            "entity_id": session_id,
        }
        async for delta in stream_answer_with_meta(self.provider, prompt, audit_ctx, meta_sink):
            yield delta

    async def _get_skill(self) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(self.db, "TutorAgent")
        except Exception:
            return ""
