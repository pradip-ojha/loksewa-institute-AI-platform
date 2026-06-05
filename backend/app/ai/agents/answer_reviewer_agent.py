"""Reviewer / verification pass over a first-pass evaluation (CLAUDE.md §12).

A second GPT-5.5 call for demo-quality reliability: checks fairness, enforces the
configured max marks, prunes unnecessary annotations, and tightens feedback. It
returns a corrected evaluation in the SAME shape as the evaluator output, so the
pipeline can store it as the reviewed result (the pre-review result is kept for
audit).
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
2. Enforce the configured FULL MARKS per question — no awarded mark may exceed it.
3. Remove unnecessary or noisy annotations. Keep ONLY annotations tied to a specific wrong written item (wrong sentence/formula/calculation/keyword, contradiction, irrelevant line). Drop annotations for missing points / weak explanation / structure / general advice.
4. Keep feedback concise and useful; fix anything unfair or unclear.
5. Do not invent new line ids — only keep annotations whose "line" id appears in the first-pass evaluation.

FULL MARKS PER QUESTION:
{full_marks_block}

FIRST-PASS EVALUATION (JSON):
{evaluation_json}

Return ONLY valid JSON in exactly this structure (one entry per question), plus a short overall note:
{{
  "questions": [
    {{"qid": "Q1", "m": 6, "fm": 8, "fb": "...", "mistakes": ["..."], "ann": [{{"t": "underline", "line": "p1_L4", "c": "..."}}], "confidence": 0.8}}
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
            evaluation_json=json.dumps(evaluation, ensure_ascii=False)[:48000],
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
        if not isinstance(result, dict) or not isinstance(result.get("questions"), list):
            raise AIResponseError("answer review did not return a 'questions' list")
        return result
