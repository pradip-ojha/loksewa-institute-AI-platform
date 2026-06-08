"""Reviewer / verification pass over the checker's evaluation (CLAUDE.md §12).

A second GPT-5.5 call for demo-quality reliability: checks fairness, enforces the
configured max marks, tightens feedback, and PRUNES annotation targets that aren't
genuinely wrong written text. It returns a corrected evaluation in the SAME shape as
the checker output so the pipeline can store it as the reviewed result (the
pre-review result is kept for audit). It does not rewrite everything unnecessarily.
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

REVIEW_PROMPT = """You are a senior examiner doing a verification pass over a junior checker's marking. Produce a corrected, final evaluation.

YOUR JOB:
1. Check the marks are fair and internally consistent across questions.
2. Enforce the configured MAX MARKS per question — no awarded mark may exceed it.
3. Keep the section-wise breakdown ("sections") consistent: each section's awarded ≤ its max, and the sections' awarded marks SUM to the question's awarded_marks. Keep at least one correct/partial section with evidence_text where the answer has any correct content (positive marking).
4. Prune annotation_targets: keep ONLY targets tied to a specific wrong written item (wrong sentence/formula/calculation step/number/keyword, contradiction, irrelevant line). Drop targets for missing points / weak explanation / structure / general advice. Do NOT invent new targets, and keep each target's "target_text" exactly as given.
5. Keep feedback concise and useful; fix anything unfair or unclear.
6. Do not rewrite things that are already fine. Preserve the "sections" array shape.

MAX MARKS PER QUESTION:
{full_marks_block}

CHECKER EVALUATION (JSON):
{evaluation_json}

Return ONLY valid JSON in exactly this structure (same shape as the input), plus a short overall note:
{{
  "total_awarded_marks": 0,
  "total_full_marks": 0,
  "overall_summary": "...",
  "question_results": [
    {{"question_number": "1", "page_numbers": [1], "awarded_marks": 6, "max_marks": 10,
      "feedback": "...", "missing_points": ["..."], "confidence": 0.8,
      "sections": [{{"section": "Definition", "max_marks": 2, "awarded_marks": 2, "status": "correct", "evidence_text": "..."}}],
      "annotation_targets": [{{"page_number": 1, "question_number": "1", "target_text": "...",
        "comment_text": "...", "annotation_action": "underline_with_comment"}}]}}
  ],
  "review_notes": "one or two sentences on what was adjusted"
}}"""


class AnswerReviewerAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("reasoning")

    async def review(
        self, *, evaluation: dict, full_marks_by_qid: dict[str, int], sheet_id: uuid.UUID,
    ) -> dict:
        import json

        full_marks_block = "\n".join(f"- {qid}: {fm}" for qid, fm in full_marks_by_qid.items()) or "none"
        prompt = REVIEW_PROMPT.format(
            full_marks_block=full_marks_block,
            evaluation_json=json.dumps(evaluation, ensure_ascii=False)[:50000],
        )
        audit_ctx = {
            "db": self.db,
            "agent_type": "AnswerReviewerAgent",
            "task_type": "answer_review",
            "entity_type": "student_answer_sheet",
            "entity_id": sheet_id,
        }
        try:
            result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
        except Exception as exc:
            raise RuntimeError(f"Answer review failed: {exc}") from exc
        if not isinstance(result, dict) or not isinstance(result.get("question_results"), list):
            raise AIResponseError("answer review did not return a 'question_results' list")
        return result
