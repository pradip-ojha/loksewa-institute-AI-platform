"""Clean a merged Nepali / Nepali-English lecture transcript without changing meaning.

Fixes broken sentence flow, punctuation, repeated words, and obvious transcription
artifacts; preserves teacher meaning, examples, technical terms, numbers, dates, and
Loksewa-specific terms. It NEVER summarizes — that is the summary agent's job.
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

CLEAN_PROMPT = """You clean raw speech-to-text transcripts of Nepali Loksewa lecture videos.

The transcript is Nepali, English, or a Nepali-English mix. Clean it WITHOUT changing meaning:
- Fix broken sentence flow, punctuation, and sentence boundaries.
- Remove obvious repeated words and transcription artifacts (filler stutters, duplicated phrases).
- Keep the teacher's meaning, examples, technical terms, numbers, dates, and Loksewa-specific terms EXACTLY.
- Preserve Devanagari exactly. Do NOT translate. Do NOT summarize or shorten the content.
- Keep the natural teaching order of the lecture.

Active skill instructions:
{skill_instructions}

Admin custom instruction (may be 'none'):
{custom_instruction}

RAW TRANSCRIPT:
{transcript}

Return ONLY valid JSON in exactly this structure:
{{"cleaned_transcript": "the full cleaned transcript text", "language": "nepali" | "english" | "nepali_english_mixed"}}"""


class VideoTranscriptCleanerAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("reasoning")

    async def clean(self, *, raw_transcript: str, custom_instruction: str | None, video_id: uuid.UUID) -> dict:
        skill = await self._get_skill()
        prompt = CLEAN_PROMPT.format(
            skill_instructions=skill,
            custom_instruction=custom_instruction or "none",
            transcript=raw_transcript[:60000],
        )
        audit_ctx = {
            "db": self.db,
            "agent_type": "VideoTranscriptCleanerAgent",
            "task_type": "video_transcript_cleaning",
            "entity_type": "video",
            "entity_id": video_id,
        }
        try:
            result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
        except Exception as exc:
            raise RuntimeError(f"Transcript cleaning failed: {exc}") from exc
        if not isinstance(result, dict) or not result.get("cleaned_transcript"):
            raise AIResponseError("transcript cleaning did not return cleaned_transcript")
        return result

    async def _get_skill(self) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(self.db, "VideoTranscriptCleanerAgent")
        except Exception:
            return "Clean Nepali/English lecture transcripts: fix flow and punctuation, preserve meaning and terms, never summarize."
