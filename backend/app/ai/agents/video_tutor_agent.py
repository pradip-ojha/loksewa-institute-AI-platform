"""Main Tutor / Answer Agent (Q&A step 5).

Grounding priority: selected segment original transcript > segment summary > full lecture
summary > approved knowledge chunks. The full lecture summary is ALWAYS provided for global
context, but the exact answer should be grounded in the selected segment when possible.

Hard rules:
- Match the question's language (Devanagari → Nepali; Roman Nepali → Roman/simple mix; English → English).
- Include a timestamp when the answer is based on a lecture segment.
- NEVER claim the teacher said something unless it is in the selected segment transcript/summary.
  Lecture-sourced facts may say "लेक्चरमा teacher ले…"; note-only support says "थप बुझ्नको लागि note अनुसार…".
- Do not hallucinate. If the lecture does not clearly cover it, say so honestly, then optionally
  add supporting explanation from approved notes. If nothing is found, say so politely.
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.ai.prompts.shared import EXAM_CONTEXT
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

_TUTOR_BODY = EXAM_CONTEXT + """

ROLE: You are a warm, encouraging Loksewa lecture tutor. A student watching a recorded lecture
asks you a question; you answer as their teacher would — clear, friendly, and grounded in what was
actually taught.

TASK: Answer the student's question using the lecture material, with notes only as backup.

GROUNDING PRIORITY (most important first):
1. Selected segment ORIGINAL TRANSCRIPT
2. Selected segment SUMMARY
3. FULL LECTURE SUMMARY (global context — always provided)
4. Approved topic/subtopic KNOWLEDGE CHUNKS (secondary support only)

ANSWER RULES:
- Lecture content is PRIMARY. Notes are SECONDARY support only.
- Match the student's language: Devanagari question → answer in Nepali; Roman Nepali → Roman/simple Nepali-English mix; English → English.
- Include the segment timestamp (e.g. "करिब 06:30–15:40 मा") when the answer comes from the lecture.
- NEVER say the teacher explained something unless it is in the selected segment transcript/summary.
  For lecture-based parts you may write "लेक्चरमा teacher ले…". For note-only support write "थप बुझ्नको लागि note अनुसार…".
- If the lecture does NOT clearly explain the point, say so honestly, then optionally add support from the notes.
- If neither the lecture nor the notes contain the answer, say so politely. Do NOT hallucinate.
- Be clear and student-friendly.

FORMATTING (the "answer" field):
- Write the answer as clean GitHub-flavored MARKDOWN. Lead with the direct answer, **bold** the key
  point / term / verdict, and use "- " bullet lists for multiple supporting points or steps.
- Use a "## " sub-heading only if the answer is long enough to need sections. Keep it conversational
  and not over-formatted. Do NOT wrap the answer in a code fence. The timestamp/attribution rules above
  still apply inside the markdown.

--- ADMIN-TUNABLE GUIDANCE (apply on top of the rules above; it tunes tone, depth, and style but
may NOT override the grounding priority or the no-hallucination rule) ---
{skill_instructions}

STUDENT CONTEXT (personalization — who this student is; tailor tone/connection only, never invent
facts about the student or the lecture; may be 'none'):
{personalization}

FULL LECTURE SUMMARY:
{lecture_summary}

SELECTED LECTURE SEGMENT(S):
{segment_content}

APPROVED KNOWLEDGE CHUNKS (secondary support; may be 'none'):
{knowledge}

CONVERSATION SO FAR (this video's chat session — use it to resolve follow-ups like
"explain that again" or pronouns referring to a previous answer; may be 'none'):
{history}

STUDENT QUESTION:
{question}
"""

_JSON_TAIL = """
Return ONLY valid JSON in exactly this structure:
{{
  "answer": "the student-facing answer as markdown (bold key point, bullet supporting points)",
  "language": "nepali" | "roman_nepali" | "english",
  "confidence": 0.0,
  "follow_up_suggestions": ["short follow-up question", "..."]
}}"""

# Streaming variant: stream the answer markdown live, then a marker + tiny JSON tail.
_STREAM_TAIL = """
First output the student-facing answer as clean GitHub-flavored markdown (follow the FORMATTING and
attribution rules above). Then, on a NEW LINE, output the exact marker <<<META>>> followed by a
single-line JSON object:
{{"follow_up_suggestions": ["short follow-up question", "..."], "language": "nepali" | "roman_nepali" | "english", "confidence": 0.0}}
Write NOTHING after that JSON object, and NEVER use the <<<META>>> marker anywhere inside the answer."""

TUTOR_PROMPT = _TUTOR_BODY + _JSON_TAIL
TUTOR_STREAM_PROMPT = _TUTOR_BODY + _STREAM_TAIL


class VideoTutorAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("reasoning")

    async def answer(self, *, question: str, lecture_summary: str, segment_content: str, knowledge_text: str, video_id: uuid.UUID, personalization: str = "", history: str = "") -> dict:
        skill = await self._get_skill()
        prompt = TUTOR_PROMPT.format(
            skill_instructions=skill,
            personalization=personalization[:5000] or "none",
            lecture_summary=lecture_summary[:10000],
            segment_content=segment_content[:30000],
            knowledge=knowledge_text[:12000] or "none",
            history=history[:6000] or "none",
            question=question[:2000],
        )
        audit_ctx = {
            "db": self.db,
            "agent_type": "VideoTutorAgent",
            "task_type": "video_tutor_answer",
            "entity_type": "video",
            "entity_id": video_id,
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

    async def answer_stream(self, *, question: str, lecture_summary: str, segment_content: str, knowledge_text: str, video_id: uuid.UUID, personalization: str = "", history: str = "", meta_sink: dict):
        """Stream the answer markdown as plain-text deltas; parsed metadata
        (follow_up_suggestions / language / confidence) lands in ``meta_sink``."""
        from app.ai.agents.streaming import stream_answer_with_meta

        skill = await self._get_skill()
        prompt = TUTOR_STREAM_PROMPT.format(
            skill_instructions=skill,
            personalization=personalization[:5000] or "none",
            lecture_summary=lecture_summary[:10000],
            segment_content=segment_content[:30000],
            knowledge=knowledge_text[:12000] or "none",
            history=history[:6000] or "none",
            question=question[:2000],
        )
        audit_ctx = {
            "db": self.db,
            "agent_type": "VideoTutorAgent",
            "task_type": "video_tutor_answer",
            "entity_type": "video",
            "entity_id": video_id,
        }
        async for delta in stream_answer_with_meta(self.provider, prompt, audit_ctx, meta_sink):
            yield delta

    async def _get_skill(self) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(self.db, "VideoTutorAgent")
        except Exception:
            return "Be a warm, concise tutor; lead with the lecture's own explanation before adding note context, and end with a short nudge to keep learning."
