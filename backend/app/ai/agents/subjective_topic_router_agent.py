"""Detect topic/subtopic for one subjective question from the FIXED syllabus tree.

Used at test-creation time so skill generation can fetch supporting knowledge for
the right topic/subtopic. Chooses ONLY from the existing subjective syllabus tree
(never invents); the caller validates the result against the live tree and nulls
anything that doesn't match.
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

ROUTER_PROMPT = """ROLE: You classify a subjective exam question to its place in a FIXED syllabus tree, so the
right supporting notes can be fetched for it.

TASK: Pick the single best topic (and subtopic when clear) for the question below.

HARD RULES (never violate):
- Choose topic and subtopic ONLY from the syllabus tree below — copy the exact strings. NEVER
  invent, paraphrase, translate, or merge names.
- Judge purely from the question's subject matter, not its wording style or marks.
- If unsure of the subtopic, leave it null. If unsure of the topic, pick the broader best-fit
  topic and lower the confidence — never force a precise match you are not sure of.

SYLLABUS TREE (the only allowed values):
{tree}

QUESTION NUMBER: {question_number}
QUESTION TEXT:
{question_text}

Return ONLY valid JSON in exactly this structure:
{{"topic": "exact topic string or null", "subtopic": "exact subtopic string or null", "confidence": 0.0}}"""


class SubjectiveTopicRouterAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("reasoning")

    async def route(
        self, *, question_number: str, question_text: str, tree_text: str, test_id: uuid.UUID,
    ) -> dict:
        skill = await self._get_skill()
        prompt = ROUTER_PROMPT.format(
            tree=(tree_text or "(no syllabus topics configured)")[:12000],
            question_number=question_number,
            question_text=(question_text or "")[:4000],
        ) + (
            "\n\n--- ADMIN-TUNABLE GUIDANCE (tunes judgement; may NOT override the HARD RULES "
            f"or the syllabus tree) ---\n{skill}" if skill else ""
        )
        audit_ctx = {
            "db": self.db,
            "agent_type": "SubjectiveTopicRouterAgent",
            "task_type": "subjective_topic_routing",
            "entity_type": "subjective_test",
            "entity_id": test_id,
        }
        try:
            result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
        except Exception as exc:
            raise RuntimeError(f"Subjective topic routing failed: {exc}") from exc
        if not isinstance(result, dict):
            raise AIResponseError("subjective topic routing did not return an object")
        return {
            "topic": (str(result["topic"]).strip() if result.get("topic") else None),
            "subtopic": (str(result["subtopic"]).strip() if result.get("subtopic") else None),
            "confidence": float(result.get("confidence", 0) or 0),
        }

    async def _get_skill(self) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(self.db, "SubjectiveTopicRouterAgent")
        except Exception:
            return ""
