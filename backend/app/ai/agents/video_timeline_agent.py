"""Generate the lecture TIMELINE — the primary retrieval index for Q&A.

Splits the cleaned transcript into meaningful teaching segments (generally 3–10 min,
but a coherent teaching unit matters more than exact duration). The label and
description must be high quality because the Segment Router depends on them at Q&A time.
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

TIMELINE_PROMPT = """You build a structured TIMELINE from a Nepali Loksewa lecture transcript.
The timeline is both a UI feature and the retrieval index a router will later use to answer student questions.

Split the lecture into MEANINGFUL teaching segments:
- Each segment is one coherent teaching unit (a concept, sub-topic, worked example, or discussion).
- Aim for roughly 3–10 minutes per segment, but a meaningful unit matters more than exact duration.
- Segments must be in order and cover the whole lecture without large gaps.
- The lecture's total duration is about {duration_seconds} seconds. Keep start/end within [0, duration].

For each segment, write:
- label: short, specific title (the router routes by this — make it descriptive, not generic).
- description: 2–4 sentences describing what is taught, useful for routing a question to this segment.
- summary: a clear summary of the segment's teaching content.
- original_transcript: the portion of the transcript belonging to this segment (verbatim, Devanagari preserved).

Active skill instructions:
{skill_instructions}

Admin custom instruction (may be 'none'):
{custom_instruction}

CLEANED TRANSCRIPT:
{transcript}

Return ONLY valid JSON in exactly this structure:
{{
  "segments": [
    {{
      "start_seconds": 0,
      "end_seconds": 390,
      "label": "...",
      "description": "...",
      "summary": "...",
      "original_transcript": "...",
      "confidence": 0.9
    }}
  ]
}}"""


class VideoTimelineAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("reasoning")

    async def generate(self, *, cleaned_transcript: str, duration_seconds: int | None, custom_instruction: str | None, video_id: uuid.UUID) -> list[dict]:
        skill = await self._get_skill()
        prompt = TIMELINE_PROMPT.format(
            skill_instructions=skill,
            custom_instruction=custom_instruction or "none",
            duration_seconds=int(duration_seconds or 0),
            transcript=cleaned_transcript[:60000],
        )
        audit_ctx = {
            "db": self.db,
            "agent_type": "VideoTimelineAgent",
            "task_type": "video_timeline_generation",
            "entity_type": "video",
            "entity_id": video_id,
        }
        try:
            result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
        except Exception as exc:
            raise RuntimeError(f"Timeline generation failed: {exc}") from exc
        if not isinstance(result, dict) or not isinstance(result.get("segments"), list):
            raise AIResponseError("timeline generation did not return a 'segments' list")

        out: list[dict] = []
        for seg in result["segments"]:
            if not isinstance(seg, dict):
                continue
            label = str(seg.get("label") or "").strip()
            if not label:
                continue
            out.append({
                "start_seconds": float(seg.get("start_seconds", 0) or 0),
                "end_seconds": float(seg.get("end_seconds", 0) or 0),
                "label": label[:300],
                "description": str(seg.get("description") or "").strip(),
                "summary": str(seg.get("summary") or "").strip(),
                "original_transcript": str(seg.get("original_transcript") or "").strip(),
                "confidence": float(seg.get("confidence", 0) or 0),
            })
        if not out:
            raise AIResponseError("timeline generation produced no usable segments")
        return out

    async def _get_skill(self) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(self.db, "VideoTimelineAgent")
        except Exception:
            return "Split the lecture into meaningful teaching segments with high-quality labels and descriptions for routing."
