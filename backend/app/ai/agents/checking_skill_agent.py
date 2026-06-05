"""Generate the internal per-question checking guide for a subjective test.

Auto-generated at test creation (no admin approval). The guide is enrichment the
evaluator layers on top of the live admin test config (paper, marks, rubric or
default, custom instruction). Grounded in the model answer + rubric so it never
invents a marking scheme the admin didn't configure.
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

SKILL_PROMPT = """You are an expert exam copy-checking strategist. Produce a concise, structured CHECKING GUIDE for ONE subjective question, so an AI evaluator can mark student answers fairly and consistently.

QUESTION NUMBER: {question_number}
FULL MARKS: {marks}
QUESTION TEXT:
{question_text}

MODEL / IDEAL ANSWER (may be empty):
{model_answer}

MARKING RUBRIC (admin-provided if present, otherwise the platform default):
{rubric}

ADMIN CUSTOM CHECKING INSTRUCTION (highest priority — may be empty):
{custom_instruction}

Active skill instructions:
{skill_instructions}

Build the guide ONLY from the question, model answer, rubric, and admin instruction above — do not invent a different marking scheme. Distribute marks so they sum to the full marks.

Return ONLY valid JSON in exactly this structure:
{{
  "required_points": ["key point worth marks", "..."],
  "marks_distribution": [{{"point": "...", "marks": 2}}],
  "partial_marking_rules": "how to award partial credit",
  "expected_keywords": ["...", "..."],
  "common_mistakes": ["...", "..."],
  "feedback_style": "short guidance on tone/length of feedback",
  "annotation_hints": "what specific wrong items would justify a line annotation",
  "confidence_hints": "when to lower confidence"
}}"""


class CheckingSkillAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("reasoning")

    async def generate(
        self, *, question_number: str, question_text: str, marks: int,
        model_answer: str, rubric: str, custom_instruction: str | None,
        test_id: uuid.UUID,
    ) -> dict:
        skill = await self._get_skill()
        prompt = SKILL_PROMPT.format(
            question_number=question_number,
            marks=marks,
            question_text=question_text[:6000],
            model_answer=(model_answer or "Not provided.")[:8000],
            rubric=(rubric or "Not provided.")[:6000],
            custom_instruction=custom_instruction or "none",
            skill_instructions=skill,
        )
        audit_ctx = {
            "db": self.db,
            "agent_type": "CheckingSkillAgent",
            "task_type": "question_specific_skill_generation",
            "entity_type": "subjective_test",
            "entity_id": test_id,
        }
        try:
            result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
        except Exception as exc:
            raise RuntimeError(f"Checking-skill generation failed: {exc}") from exc
        if not isinstance(result, dict):
            raise AIResponseError("checking-skill generation did not return a JSON object")
        return result

    async def _get_skill(self) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(self.db, "CheckingSkillAgent")
        except Exception:
            return "Produce a fair, rubric-grounded per-question checking guide. Distribute marks to sum to full marks."
