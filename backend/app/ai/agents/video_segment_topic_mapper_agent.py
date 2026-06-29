"""Map each timeline segment to topic/subtopic from the FIXED chapter syllabus tree.

Only existing topics/subtopics may be chosen — never invented. A segment may map to
multiple subtopics. Uncertain mappings get a low confidence and may use a broader topic.
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

MAP_PROMPT = """ROLE: You map lecture timeline segments to the institute's FIXED chapter syllabus, so each
segment can be linked to the right supporting notes.

HARD RULES (never violate):
- Choose topic and subtopics ONLY from the provided syllabus tree — copy exact strings. NEVER
  invent, paraphrase, translate, or merge names.
- A segment may map to multiple subtopics, or to a topic with no subtopic.
- If a segment does not clearly match any topic, set topic to null rather than forcing a fit.
- If unsure of the precise subtopic, pick the broader best-fit topic and lower the confidence.

SYLLABUS TREE (the only allowed topic/subtopic values):
{tree}

SEGMENTS (index, label, description):
{segments}

Return ONLY valid JSON in exactly this structure:
{{
  "mappings": [
    {{"segment_index": 0, "topic": "exact topic string or null", "subtopic_ids": ["exact subtopic string", ...], "confidence": 0.0}}
  ]
}}"""


class VideoSegmentTopicMapperAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("thinking")

    async def map_segments(self, *, segments: list[dict], tree_text: str, video_id: uuid.UUID) -> dict[int, dict]:
        skill = await self._get_skill()
        seg_block = "\n".join(
            f"[{i}] {s.get('label', '')} — {s.get('description', '')}" for i, s in enumerate(segments)
        )[:30000]
        prompt = MAP_PROMPT.format(tree=tree_text[:12000], segments=seg_block)
        audit_ctx = {
            "db": self.db,
            "agent_type": "VideoSegmentTopicMapperAgent",
            "task_type": "video_segment_topic_mapping",
            "entity_type": "video",
            "entity_id": video_id,
        }
        # Skill is advisory context; keep the prompt grounded in the tree.
        if skill:
            prompt = (
                f"{prompt}\n\n--- ADMIN-TUNABLE GUIDANCE (tunes judgement; may NOT override the "
                f"HARD RULES or the syllabus tree) ---\n{skill}"
            )
        try:
            result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
        except Exception as exc:
            raise RuntimeError(f"Segment topic mapping failed: {exc}") from exc
        if not isinstance(result, dict) or not isinstance(result.get("mappings"), list):
            raise AIResponseError("segment topic mapping did not return a 'mappings' list")

        out: dict[int, dict] = {}
        for m in result["mappings"]:
            if not isinstance(m, dict):
                continue
            try:
                idx = int(m.get("segment_index"))
            except (TypeError, ValueError):
                continue
            subs = m.get("subtopic_ids")
            out[idx] = {
                "topic": (m.get("topic") or None),
                "subtopic_ids": [str(s) for s in subs] if isinstance(subs, list) else [],
                "confidence": float(m.get("confidence", 0) or 0),
            }
        return out

    async def _get_skill(self) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(self.db, "VideoSegmentTopicMapperAgent")
        except Exception:
            return ""
