"""Generate the full lecture summary — passed into EVERY student Q&A request.

Produces short + detailed summaries, key points, exam-focused points, important terms,
and a bank of possible exam questions (MCQs, short-answer, long-answer).
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = """You write a complete study summary of a Nepali Loksewa lecture from its transcript.
This summary is the GLOBAL context for a tutor that answers student questions, so make it faithful and complete.

Preserve Devanagari and Loksewa terms. Do not invent facts not present in the lecture.

Active skill instructions:
{skill_instructions}

Admin custom instruction (may be 'none'):
{custom_instruction}

CLEANED TRANSCRIPT:
{transcript}

Return ONLY valid JSON in exactly this structure:
{{
  "short_summary": "2-4 sentence overview of the whole lecture",
  "detailed_summary": "thorough multi-paragraph summary of everything taught",
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
        return result

    async def _get_skill(self) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(self.db, "VideoSummaryAgent")
        except Exception:
            return "Summarize the lecture faithfully and completely; include key points, exam focus, terms, and possible questions."
