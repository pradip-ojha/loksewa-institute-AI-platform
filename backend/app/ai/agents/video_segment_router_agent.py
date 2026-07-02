"""Segment Router (Q&A step 1): pick the 1–3 timeline segments most relevant to a question.

Routes by label/description meaning; for vague questions ("यो point explain गर्नु") it
uses the student's current video time to choose nearby segments. Returns segment ids,
a confidence, and a short reason. Avoids selecting too many segments.
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

ROUTER_PROMPT = """ROLE: You route a student's question to the most relevant lecture timeline segment(s). Pick
the smallest set that actually contains the answer, so the tutor reads focused context.

HARD RULES (never violate):
- If the question clearly names a concept, route by segment label/description meaning.
- If the question is vague (e.g. "यो point फेरि explain गर्नु"), use CURRENT VIDEO TIME to choose the
  segment the student is currently watching and its neighbours.
- Select exactly 1 segment when confident; 2–3 only when genuinely unsure. NEVER more than 3.

--- ADMIN-TUNABLE GUIDANCE (refines emphasis only; never overrides the HARD RULES above) ---
{skill_instructions}

CURRENT VIDEO TIME: {current_time}

TIMELINE SEGMENTS (id | time range | label | description):
{segments}

STUDENT QUESTION:
{question}

Return ONLY valid JSON in exactly this structure:
{{"selected_segment_ids": ["seg_002"], "confidence": 0.0, "reason": "short reason"}}"""


class VideoSegmentRouterAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("thinking")

    async def _get_skill(self) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(self.db, "VideoSegmentRouterAgent")
        except Exception:
            return "Prefer the single best segment; widen to 2–3 only when the question genuinely spans them. For vague questions, trust the current video time."

    async def route(self, *, question: str, current_time: str | None, segments_block: str, video_id: uuid.UUID) -> dict:
        prompt = ROUTER_PROMPT.format(
            current_time=current_time or "unknown",
            segments=segments_block[:16000],
            question=question[:2000],
            skill_instructions=await self._get_skill(),
        )
        audit_ctx = {
            "db": self.db,
            "agent_type": "VideoSegmentRouterAgent",
            "task_type": "video_segment_routing",
            "entity_type": "video",
            "entity_id": video_id,
        }
        try:
            result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
        except Exception as exc:
            raise RuntimeError(f"Segment routing failed: {exc}") from exc
        if not isinstance(result, dict) or not isinstance(result.get("selected_segment_ids"), list):
            raise AIResponseError("segment routing did not return selected_segment_ids")
        return {
            "selected_segment_ids": [str(s) for s in result["selected_segment_ids"]][:3],
            "confidence": float(result.get("confidence", 0) or 0),
            "reason": str(result.get("reason") or ""),
        }
