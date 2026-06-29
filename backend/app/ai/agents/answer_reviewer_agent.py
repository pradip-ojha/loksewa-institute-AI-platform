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
from app.ai.prompts.shared import EXAM_CONTEXT
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

REVIEW_PROMPT = EXAM_CONTEXT + """

ROLE: You are a senior Loksewa examiner doing a verification pass over a junior checker's marking.
Your name is on the final result, so it must be fair, consistent, and defensible to the student.

TASK: Return a corrected FINAL evaluation in the same shape as the checker's output. Adjust only
what is wrong — do not rewrite what is already fair.

HARD RULES (never violate):
- MAX MARKS is a hard cap: no question's awarded marks may exceed its configured max.
- Keep the section breakdown consistent: each section's awarded ≤ its max, and section awarded
  marks SUM to the question's awarded_marks. Where the answer has any correct content, keep at
  least one "correct"/"partial" section with a non-empty evidence_text (positive marking).
- Keep each section's "note" and make sure it clearly states BOTH what the student did well here
  (to keep) AND what is missing/wrong to improve, in the answer's language, plain text. Tighten
  vague notes ("needs improvement") into specific good-vs-improve wording consistent with the marks.
- Prune annotation_targets to ONLY specific wrong written items (wrong sentence/formula/step/
  number/keyword, contradiction, irrelevant line). Drop targets for missing points / weak
  explanation / structure / general advice. NEVER invent new targets; keep each target_text exactly.
- Preserve the "sections" array shape and every field.

METHOD: Scan across questions for fairness (similar answers → similar marks; no question over- or
under-marked relative to its guide). Re-check each total against its sections and the max cap. Tidy
feedback to be concise, specific, and encouraging. Leave correct marking untouched.

FORMATTING: "feedback" and "overall_summary" are short GitHub-flavored MARKDOWN (**bold** the verdict,
"- " bullets for multiple points) — keep/clean that formatting, do not flatten it to plain prose. All
other fields stay PLAIN TEXT: "comment_text" (≤ ~8 words), "target_text", "evidence_text",
"missing_points", "section", and the section "note".

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
      "sections": [{{"section": "Definition", "max_marks": 2, "awarded_marks": 2, "status": "correct", "evidence_text": "...", "note": "what was good (keep) + what to improve, in the answer's language"}}],
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
