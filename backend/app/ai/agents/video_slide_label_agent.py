"""Generate slide labels for the lecture support-slides PDF, aligned to the timeline.

Each slide gets a semantic title, the timeline timestamps it relates to, its topics,
and a short summary. Aligns slide content to the timeline segments by meaning.
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

SLIDE_PROMPT = """You label lecture support slides and align them to the lecture timeline.

You are given the per-slide text (extracted from the slides PDF) and the lecture timeline
(segments with their time ranges and labels). For each slide, produce a semantic label and
align it to the timeline timestamps it most relates to (by meaning).

LECTURE TIMELINE (segment label — time range):
{timeline}

SLIDES (slide number → extracted text):
{slides}

Return ONLY valid JSON in exactly this structure:
{{
  "slides": [
    {{
      "slide_number": 1,
      "title": "short semantic title",
      "related_timestamps": ["00:03:20-00:05:10"],
      "topics": ["..."],
      "summary": "1-2 sentence summary of the slide"
    }}
  ]
}}"""


class VideoSlideLabelAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("reasoning")

    async def generate(self, *, slides_block: str, timeline_block: str, video_id: uuid.UUID) -> list[dict]:
        prompt = SLIDE_PROMPT.format(timeline=timeline_block[:12000], slides=slides_block[:40000])
        audit_ctx = {
            "db": self.db,
            "agent_type": "VideoSlideLabelAgent",
            "task_type": "video_slide_labeling",
            "entity_type": "video",
            "entity_id": video_id,
        }
        try:
            result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
        except Exception as exc:
            raise RuntimeError(f"Slide labeling failed: {exc}") from exc
        if not isinstance(result, dict) or not isinstance(result.get("slides"), list):
            raise AIResponseError("slide labeling did not return a 'slides' list")

        out: list[dict] = []
        for s in result["slides"]:
            if not isinstance(s, dict):
                continue
            try:
                num = int(s.get("slide_number"))
            except (TypeError, ValueError):
                num = len(out) + 1
            ts = s.get("related_timestamps")
            topics = s.get("topics")
            out.append({
                "slide_number": num,
                "title": str(s.get("title") or "").strip()[:300],
                "related_timestamps": [str(t) for t in ts] if isinstance(ts, list) else [],
                "topics": [str(t) for t in topics] if isinstance(topics, list) else [],
                "summary": str(s.get("summary") or "").strip(),
            })
        return out
