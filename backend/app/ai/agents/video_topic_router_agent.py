"""Topic/Subtopic Router (Q&A step 3): pick topic + subtopics from the FIXED syllabus tree.

Chooses ONLY from the existing tree (never invents). If uncertain, picks a broader topic
with a low confidence. The result filters which approved knowledge chunks support the answer.
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

ROUTER_PROMPT = """ROLE: You select the topic/subtopic for a student's question from a FIXED syllabus tree, so the
right approved notes can be retrieved to support the answer.

HARD RULES (never violate):
- Choose topic and subtopics ONLY from the syllabus tree below — copy exact strings. NEVER invent,
  paraphrase, translate, or merge names.
- Use the lecture summary and selected segment context to disambiguate the question's subject.
- If unsure of the subtopic, leave subtopics empty; if unsure of the topic, pick the broader
  best-fit topic and lower the confidence.

SYLLABUS TREE (the only allowed values):
{tree}

FULL LECTURE SUMMARY (global context):
{lecture_summary}

SELECTED SEGMENT CONTEXT:
{segment_context}

STUDENT QUESTION:
{question}

Return ONLY valid JSON in exactly this structure:
{{"topic": "exact topic string or null", "subtopic_ids": ["exact subtopic string", ...], "confidence": 0.0}}"""


class VideoTopicRouterAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("reasoning")

    async def route(self, *, question: str, lecture_summary: str, segment_context: str, tree_text: str, video_id: uuid.UUID) -> dict:
        prompt = ROUTER_PROMPT.format(
            tree=tree_text[:12000],
            lecture_summary=lecture_summary[:8000],
            segment_context=segment_context[:8000],
            question=question[:2000],
        )
        audit_ctx = {
            "db": self.db,
            "agent_type": "VideoTopicRouterAgent",
            "task_type": "video_topic_routing",
            "entity_type": "video",
            "entity_id": video_id,
        }
        try:
            result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
        except Exception as exc:
            raise RuntimeError(f"Topic routing failed: {exc}") from exc
        if not isinstance(result, dict):
            raise AIResponseError("topic routing did not return an object")
        subs = result.get("subtopic_ids")
        return {
            "topic": result.get("topic") or None,
            "subtopic_ids": [str(s) for s in subs] if isinstance(subs, list) else [],
            "confidence": float(result.get("confidence", 0) or 0),
        }
