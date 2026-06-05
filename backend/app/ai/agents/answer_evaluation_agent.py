"""Evaluate reconstructed question-wise answers against the admin-configured test.

Inputs (priority order): admin custom instruction > selected rubric > DEFAULT_RUBRIC,
all bounded by the per-question full marks (a hard cap enforced again in code via
service.clamp_marks). Emits the compact evaluation JSON from CLAUDE.md §12,
including annotation instructions that reference extracted line ids.
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

EVAL_PROMPT = """You are an expert, fair exam copy-checker. Evaluate each question's handwritten answer and award marks.

ABSOLUTE RULES:
- The configured FULL MARKS for each question is the maximum you may award. Never exceed it.
- Guidance priority when rules conflict: ADMIN CUSTOM INSTRUCTION > RUBRIC > general judgement.
- Award partial marks fairly. Accept correct ideas in the student's own words.
- Keep feedback concise and useful.

ANNOTATION RULES (very important):
- Use a line annotation ONLY for a specific WRONG written item: wrong sentence, wrong formula, wrong calculation step, wrong keyword, contradictory statement, or irrelevant line. Reference the line by its "id".
- Do NOT create line annotations for missing points, short answers, weak explanations, missing examples, poor structure, or general improvement — put those in "fb" (feedback) instead.
- Prefer few, meaningful annotations. Do not overcrowd.

ADMIN CUSTOM CHECKING INSTRUCTION (highest priority; may be 'none'):
{custom_instruction}

MARKING RUBRIC:
{rubric}

Active skill instructions:
{skill_instructions}

QUESTIONS, THEIR CHECKING GUIDES, AND THE STUDENT'S RECONSTRUCTED ANSWERS:
{questions_block}

Return ONLY valid JSON in exactly this structure (one entry per question):
{{
  "questions": [
    {{
      "qid": "Q1",
      "m": 6,
      "fm": 8,
      "fb": "Concise feedback.",
      "mistakes": ["..."],
      "ann": [
        {{"t": "underline", "line": "p1_L4", "c": "wrong formula"}},
        {{"t": "comment", "text": "..."}}
      ],
      "confidence": 0.8
    }}
  ]
}}"""


def _format_questions_block(reconstructed: dict, skills_by_qid: dict[str, dict]) -> str:
    parts: list[str] = []
    for q in reconstructed.get("questions", []):
        qid = q.get("qid")
        skill = skills_by_qid.get(qid) or {}
        lines = q.get("lines") or []
        line_refs = "\n".join(
            f'      [{ln.get("id")}] {ln.get("text", "")}' for ln in lines if ln.get("text")
        ) or "      (no legible lines extracted for this question)"
        parts.append(
            f"━━━ {qid} (FULL MARKS: {q.get('marks', 0)}) ━━━\n"
            f"QUESTION: {q.get('question_text', '')}\n"
            f"CHECKING GUIDE: {skill}\n"
            f"STUDENT ANSWER LINES (id → text):\n{line_refs}"
        )
    return "\n\n".join(parts)


class AnswerEvaluationAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("reasoning")

    async def evaluate(
        self, *, reconstructed: dict, skills_by_qid: dict[str, dict],
        rubric_text: str | None, custom_instruction: str | None, sheet_id: uuid.UUID,
    ) -> dict:
        skill = await self._get_skill()
        prompt = EVAL_PROMPT.format(
            custom_instruction=custom_instruction or "none",
            rubric=rubric_text or DEFAULT_RUBRIC,
            skill_instructions=skill,
            questions_block=_format_questions_block(reconstructed, skills_by_qid)[:48000],
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
        if not isinstance(result, dict) or not isinstance(result.get("questions"), list):
            raise AIResponseError("answer evaluation did not return a 'questions' list")
        return result

    async def _get_skill(self) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(self.db, "AnswerEvaluationAgent")
        except Exception:
            return "Mark fairly within full marks, give concise feedback, annotate only specific wrong items."
