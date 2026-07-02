"""Generate the full lecture summary — passed into EVERY student Q&A request.

Produces short + detailed summaries, key points, exam-focused points, important terms,
and a bank of possible exam questions (MCQs, short-answer, long-answer).
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.ai.prompts.shared import EXAM_CONTEXT
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = EXAM_CONTEXT + """

ROLE: You write the complete study summary of a Nepali Loksewa lecture from its transcript. This
summary is the GLOBAL context handed to the tutor on EVERY student question, and a study aid
students revise from — so it must be faithful, complete, and exam-useful.

TASK: Produce the structured summary JSON below.

HARD RULES (never violate):
- Cover everything the lecture teaches; do not invent facts that are not in the transcript.
- Preserve Devanagari and Loksewa terms exactly.
- "exam_focused_points" must reflect what a Loksewa paper would actually test from this content.

FORMATTING (applies to "detailed_summary" ONLY):
- Write "detailed_summary" as clean GitHub-flavored MARKDOWN so students can scan it like good notes.
- Use "## " sub-headings to break it into logical sections, **bold** for key terms / definitions / important
  facts, and "- " bullet lists for enumerations or steps. Keep paragraphs short.
- Do NOT wrap the whole thing in a code fence. Do NOT use markdown in any other field
  ("short_summary", list items, terms, questions stay plain text).

--- ADMIN-TUNABLE GUIDANCE (apply on top of the rules above; it tunes depth and emphasis but may
NOT override faithfulness to the lecture) ---
{skill_instructions}

Admin custom instruction (may be 'none'):
{custom_instruction}

CLEANED TRANSCRIPT:
{transcript}

Return ONLY valid JSON in exactly this structure:
{{
  "short_summary": "2-4 sentence plain-text overview of the whole lecture",
  "detailed_summary": "thorough markdown summary (## sub-headings, **bold** key terms, - bullet lists) of everything taught",
  "key_points": ["..."],
  "exam_focused_points": ["points most likely to be asked in the exam"],
  "important_terms": ["term — short meaning", "..."],
  "possible_questions": {{
    "mcqs": [{{"question": "...", "options": ["A ...", "B ...", "C ...", "D ..."], "answer": "A"}}],
    "short": ["short-answer question", "..."],
    "long": ["long-answer question", "..."]
  }}
}}

Include 5–10 MCQs, 3–5 short-answer questions, and 2–3 long-answer questions where the content supports them."""


def _as_list(v) -> list:
    """Coerce a field that must be a list into one (model sometimes returns a string or
    null). Prevents a shape drift from reaching the React renderers as a crash."""
    if isinstance(v, list):
        return v
    if v in (None, ""):
        return []
    return [v]


def _normalize_summary(result: dict) -> dict:
    """Defensive shaping: the layer only guarantees the response is a JSON object, not
    the type of each field. Coerce the list/object fields the player renders
    (key_points / exam_focused_points / important_terms / possible_questions) to their
    expected containers so malformed AI output degrades gracefully instead of crashing
    PossibleQuestionsCard / KeyPointsList."""
    for key in ("key_points", "exam_focused_points", "important_terms"):
        result[key] = _as_list(result.get(key))
    pq = result.get("possible_questions")
    if not isinstance(pq, dict):
        pq = {}
    result["possible_questions"] = {
        "mcqs": _as_list(pq.get("mcqs")),
        "short": _as_list(pq.get("short")),
        "long": _as_list(pq.get("long")),
    }
    return result


class VideoSummaryAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("reasoning")

    async def generate(self, *, cleaned_transcript: str, custom_instruction: str | None, video_id: uuid.UUID) -> dict:
        skill = await self._get_skill()
        prompt = SUMMARY_PROMPT.format(
            skill_instructions=skill,
            custom_instruction=custom_instruction or "none",
            transcript=cleaned_transcript[:60000],
        )
        audit_ctx = {
            "db": self.db,
            "agent_type": "VideoSummaryAgent",
            "task_type": "video_summary_generation",
            "entity_type": "video",
            "entity_id": video_id,
        }
        try:
            result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
        except Exception as exc:
            raise RuntimeError(f"Lecture summary generation failed: {exc}") from exc
        if not isinstance(result, dict) or not result.get("detailed_summary"):
            raise AIResponseError("lecture summary did not return a detailed_summary")
        return _normalize_summary(result)

    async def _get_skill(self) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(self.db, "VideoSummaryAgent")
        except Exception:
            return "Bias the summary toward what Loksewa actually tests; keep possible_questions realistic to the paper's style."
