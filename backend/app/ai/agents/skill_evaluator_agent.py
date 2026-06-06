"""Evaluate generated per-question checking skills for usability and correct mapping.

This is a LENIENT gate, not a perfectionist one. Its job is to catch
misconfiguration and operationally-unusable skills — NOT to demand textbook-perfect
content. A skill PASSES if it is good enough to check student answers fairly.

It FAILS a skill only for serious issues:
  • skill mapped to the wrong question / question-number mismatch
  • max-marks mismatch, or marks_breakdown not summing to full marks
  • major expected answer areas missing
  • skill too vague to check answers
  • rubric or admin instruction clearly ignored
  • numerical question lacking formula/step checking guidance
  • topic/subtopic mapping clearly wrong
  • duplicated or missing question skills

Returns a per-question verdict the orchestrator uses to decide which (if any) skills
to send back to the generator for ONE improvement pass.
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

EVAL_PROMPT = """You are a QA reviewer for AI-generated exam CHECKING GUIDES (one per question). Decide whether each guide is OPERATIONALLY USABLE to mark student answers fairly. Be LENIENT — do not demand perfect, textbook-level content. Pass a guide if it is good enough to check answers fairly.

FAIL a guide ONLY for serious issues:
- guide belongs to the wrong question, or question-number mismatch
- max_marks mismatch, or marks_breakdown does not sum to full marks
- major expected answer areas missing
- guide too vague to check answers
- rubric or admin instruction clearly ignored
- a numerical question's guide lacks formula/step-checking guidance
- topic/subtopic mapping clearly wrong
- duplicated or missing question guides

For each question check: correct question mapping, correct max marks, reasonable expected points, usable marks breakdown, partial-marking guidance, common-mistake / wrong-answer guidance, no obvious contradiction with admin instruction or rubric, and that it is a CHECKING GUIDE (not just copied textbook notes).

ADMIN CUSTOM CHECKING INSTRUCTION (may be 'none'):
{custom_instruction}

THE QUESTIONS AND THEIR GENERATED CHECKING GUIDES (JSON):
{skills_block}

Active skill instructions:
{skill_instructions}

Return ONLY valid JSON in exactly this structure (one entry per question):
{{
  "results": [
    {{"question_number": "1", "status": "passed | passed_with_warning | failed",
      "issues": ["short issue", "..."], "fix_feedback": "concrete guidance for the generator if failed/weak"}}
  ],
  "overall_notes": "one or two sentences"
}}"""


class SkillEvaluatorAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("reasoning")

    async def evaluate(
        self, *, skills: list[dict], custom_instruction: str | None, test_id: uuid.UUID,
    ) -> dict:
        """`skills` = [{question_number, marks, question_text, topic, subtopic, skill_json}]."""
        import json
        skill = await self._get_skill()
        blocks: list[str] = []
        for s in skills:
            blocks.append(
                f"━━━ Question {s.get('question_number')} (FULL MARKS: {s.get('marks')}; "
                f"TOPIC: {s.get('topic') or 'n/a'} / {s.get('subtopic') or 'n/a'}) ━━━\n"
                f"QUESTION: {str(s.get('question_text') or '')[:1500]}\n"
                f"GENERATED GUIDE: {json.dumps(s.get('skill_json') or {}, ensure_ascii=False)[:6000]}"
            )
        prompt = EVAL_PROMPT.format(
            custom_instruction=custom_instruction or "none",
            skills_block="\n\n".join(blocks)[:60000],
            skill_instructions=skill,
        )
        audit_ctx = {
            "db": self.db,
            "agent_type": "SkillEvaluatorAgent",
            "task_type": "skill_evaluation",
            "entity_type": "subjective_test",
            "entity_id": test_id,
        }
        try:
            result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
        except Exception as exc:
            raise RuntimeError(f"Skill evaluation failed: {exc}") from exc
        if not isinstance(result, dict) or not isinstance(result.get("results"), list):
            raise AIResponseError("skill evaluation did not return a 'results' list")
        return result

    async def _get_skill(self) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(self.db, "SkillEvaluatorAgent")
        except Exception:
            return "Leniently verify each checking guide is operationally usable; fail only for serious mapping/marks/coverage issues."
