"""Main checker: evaluate question-wise answers against the admin test + locked skills.

Inputs: the admin-configured test (per-question marks, optional admin instruction,
rubric or default) + the LOCKED per-question examiner skills + the student's
extracted answers. It deliberately does NOT receive large notes/books again — that
knowledge was already distilled into the locked skill at test creation, so checking
stays focused and consistent.

The checker decides WHAT is wrong, not WHERE it visually sits. It emits
`annotation_targets` keyed by the exact wrong TEXT; the vision locator finds the
geometry later. Priority on conflict: admin instruction > rubric > general judgement.
Per-question full marks are a hard cap (enforced again in service.clamp_marks).
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

# Used only when the admin selected no rubric file for the test (CLAUDE.md §11.5).
DEFAULT_RUBRIC = """DEFAULT GENERAL MARKING RUBRIC:
- Theory answers: judge concept accuracy, completeness, relevance, examples, structure, and explanation depth.
- Numerical answers: judge correct formula, steps, calculation, final answer, and units where relevant.
- Award partial marks for partially correct answers.
- Accept correct ideas expressed in the student's own words.
- Do not over-penalize spelling or grammar unless the meaning becomes unclear.
- Never award more than the full marks configured for the question."""

EVAL_PROMPT = """You are an expert, fair exam copy-checker. Evaluate each question's handwritten answer and award marks, using the locked CHECKING GUIDE for each question.

ABSOLUTE RULES:
- The configured MAX MARKS for each question is the maximum you may award. Never exceed it.
- Guidance priority when rules conflict: ADMIN CUSTOM INSTRUCTION > RUBRIC > the question's CHECKING GUIDE > general judgement.
- Award partial marks fairly per the guide's marks breakdown. Accept correct ideas in the student's own words.
- Keep feedback concise and useful. List the key missing points separately.

ANNOTATION RULES (very important — you decide WHAT is wrong, not where it is):
- Create an annotation target ONLY for a specific WRONG WRITTEN item: a wrong sentence, wrong formula, wrong calculation step, wrong number, wrong keyword, a contradictory statement, or an irrelevant line.
- Quote the wrong text EXACTLY as the student wrote it in "target_text" (so it can be found on the page). Keep it short (the wrong phrase/line only).
- Keep "comment_text" VERY short — at most ~8 words (it is drawn in the page margin). Match the answer's language (Nepali for Nepali answers).
- Do NOT create annotation targets for missing points, short answers, weak explanations, missing examples, or poor structure — those go in "feedback" and "missing_points".
- Prefer few, meaningful targets (at most ~2 per question). Do not overcrowd.

ADMIN CUSTOM CHECKING INSTRUCTION (highest priority; may be 'none'):
{custom_instruction}

MARKING RUBRIC:
{rubric}

Active skill instructions:
{skill_instructions}

QUESTIONS, THEIR LOCKED CHECKING GUIDES, AND THE STUDENT'S ANSWERS:
{questions_block}

Return ONLY valid JSON in exactly this structure (one entry per question):
{{
  "total_awarded_marks": 0,
  "total_full_marks": 0,
  "overall_summary": "Short overall summary.",
  "question_results": [
    {{
      "question_number": "1",
      "page_numbers": [1],
      "awarded_marks": 6,
      "max_marks": 10,
      "feedback": "Concise feedback.",
      "missing_points": ["..."],
      "confidence": 0.8,
      "annotation_targets": [
        {{"page_number": 1, "question_number": "1", "target_text": "exact wrong phrase from the answer",
          "comment_text": "Short correction.", "annotation_action": "underline_with_comment"}}
      ]
    }}
  ]
}}"""


def _format_questions_block(questions: list[dict], skills_by_qid: dict[str, dict]) -> str:
    import json
    parts: list[str] = []
    for q in questions:
        qid = q.get("qid")
        skill = skills_by_qid.get(qid) or {}
        pages = ", ".join(str(p) for p in q.get("page_numbers", []) or []) or "?"
        answer = q.get("answer_text") or "(no legible answer extracted for this question)"
        parts.append(
            f"━━━ Question {qid} (MAX MARKS: {q.get('marks', 0)}; pages {pages}) ━━━\n"
            f"QUESTION: {q.get('question_text', '')}\n"
            f"CHECKING GUIDE: {json.dumps(skill, ensure_ascii=False)[:6000]}\n"
            f"STUDENT ANSWER:\n{answer}"
        )
    return "\n\n".join(parts)


class AnswerEvaluationAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("reasoning")

    async def evaluate(
        self, *, questions: list[dict], skills_by_qid: dict[str, dict],
        rubric_text: str | None, custom_instruction: str | None, sheet_id: uuid.UUID,
    ) -> dict:
        """`questions` = [{qid, question_text, marks, answer_text, page_numbers}]."""
        skill = await self._get_skill()
        prompt = EVAL_PROMPT.format(
            custom_instruction=custom_instruction or "none",
            rubric=rubric_text or DEFAULT_RUBRIC,
            skill_instructions=skill,
            questions_block=_format_questions_block(questions, skills_by_qid)[:50000],
        )
        audit_ctx = {
            "db": self.db,
            "agent_type": "AnswerEvaluationAgent",
            "task_type": "answer_evaluation",
            "entity_type": "student_answer_sheet",
            "entity_id": sheet_id,
        }
        try:
            result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
        except Exception as exc:
            raise RuntimeError(f"Answer evaluation failed: {exc}") from exc
        if not isinstance(result, dict) or not isinstance(result.get("question_results"), list):
            raise AIResponseError("answer evaluation did not return a 'question_results' list")
        return result

    async def _get_skill(self) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(self.db, "AnswerEvaluationAgent")
        except Exception:
            return "Mark fairly within max marks using the locked checking guide; annotate only specific wrong written items by exact text."
