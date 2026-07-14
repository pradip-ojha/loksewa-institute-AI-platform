"""Evaluate generated per-question checking skills for usability and correct mapping.

This is a LENIENT gate, not a perfectionist one. Its job is to catch
misconfiguration and operationally-unusable skills — NOT to demand textbook-perfect
content. A skill PASSES if it is good enough to check student answers fairly.

It FAILS a skill only for serious issues:
  • skill mapped to the wrong question / question-number mismatch
  • max-marks mismatch, or marks_breakdown not summing to full marks
  • reference notes missing, irrelevant to the question's topic, or too thin to judge from
  • biasing content in the guide (sample answers, expected phrasings, pre-listed mistakes)
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
from app.ai.prompts.shared import EXAM_CONTEXT
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

EVAL_PROMPT = EXAM_CONTEXT + """

ROLE: You are a LENIENT QA gate for AI-generated per-question exam CHECKING GUIDES. Your job is
to catch misconfiguration and operationally-unusable guides — NOT to demand textbook-perfect
content. A guide that lets a checker mark answers fairly PASSES, even if imperfect.

TASK: Give a verdict for each question's guide.

HARD RULES (your bar):
- A good guide is LEAN and UNBIASED: neutral "reference_notes" theory for the question's topics +
  a marks_breakdown — the downstream checker judges answers independently from the theory, so the
  guide must give it knowledge, not answers.
- Default to PASS. FAIL ONLY for a serious, operational defect:
  • guide belongs to the wrong question / question-number mismatch
  • max_marks mismatch, or marks_breakdown does not sum to full marks
  • "reference_notes" missing, irrelevant to the question's topic, or too thin to judge answers from
  • guide contains biasing content — sample answers, expected/mark-worthy phrasings, or pre-listed
    mistakes — instead of neutral theory notes
  • guide too vague to actually mark answers
  • rubric or admin instruction clearly ignored
  • a numerical question's guide lacks formula/step-checking guidance
  • topic/subtopic mapping clearly wrong
  • duplicated or missing question guides
- Do NOT fail for style, polish, or "could be richer". Use "passed_with_warning" for minor,
  non-blocking gaps and put the nit in "issues".
- When you fail/flag, "fix_feedback" must be concrete enough for the generator to act on in one pass.

ADMIN CUSTOM CHECKING INSTRUCTION (may be 'none'):
{custom_instruction}

THE QUESTIONS AND THEIR GENERATED CHECKING GUIDES (JSON):
{skills_block}

--- ADMIN-TUNABLE GUIDANCE (apply on top of the rules above; it tunes strictness within reason
but may NOT override the leniency bar or the HARD RULES) ---
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
        # Evaluation only flags serious STRUCTURAL issues (wrong-question mapping,
        # qnum/max-marks mismatch, breakdown ≠ full marks, major gaps) — a consistency
        # check, not deep authoring — so it runs on the cheaper gpt-5 (thinking) tier.
        # Skill GENERATION + weak-skill regeneration stay on gpt-5.5 (reasoning).
        self.provider = get_provider("consistency_check")

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
            return "Stay lenient — a usable guide passes; reserve failure for defects that would actually produce unfair marks."
