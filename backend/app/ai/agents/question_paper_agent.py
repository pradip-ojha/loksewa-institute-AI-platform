"""Extract questions + per-question marks from a subjective test's question paper.

Runs at test-creation time. Marks extracted here become the source of truth for
the maximum awardable per question (CLAUDE.md §11). Extraction only — it does not
judge or answer anything.
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """You are an expert at parsing competitive-exam question papers (English, Nepali, or mixed).

Extract EVERY question from the question paper below, with its question number, full question text, and the marks allotted to it.

RULES:
1. Question numbers may look like "Q1", "1.", "प्रश्न नं. १", "१)", etc. Preserve the number label as written.
2. Marks usually appear as "[8 marks]", "(8)", "[८ अंक]", "8 marks", etc. Extract the integer marks for each question.
3. If a question has sub-parts that share one mark total, treat the whole question as one item with the total marks.
4. Preserve the question text exactly (Devanagari must be preserved). Do NOT answer or rephrase.
5. If marks for a question cannot be determined, set marks to 0.

Active skill instructions:
{skill_instructions}

Custom instruction: {custom_instruction}

QUESTION PAPER CONTENT:
{paper_text}

Return ONLY valid JSON in exactly this structure:
{{
  "questions": [
    {{"question_number": "Q1", "question_text": "...", "marks": 8}}
  ]
}}"""


class QuestionPaperAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("reasoning")

    async def extract(self, *, paper_text: str, custom_instruction: str | None, test_id: uuid.UUID) -> list[dict]:
        skill = await self._get_skill()
        prompt = EXTRACTION_PROMPT.format(
            skill_instructions=skill,
            custom_instruction=custom_instruction or "none",
            paper_text=paper_text[:40000],
        )
        audit_ctx = {
            "db": self.db,
            "agent_type": "QuestionPaperAgent",
            "task_type": "subjective_question_extraction",
            "entity_type": "subjective_test",
            "entity_id": test_id,
        }
        try:
            result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
        except Exception as exc:
            raise RuntimeError(f"Question paper extraction failed: {exc}") from exc

        if not isinstance(result, dict) or not isinstance(result.get("questions"), list):
            raise AIResponseError("question paper extraction did not return a 'questions' list")

        out: list[dict] = []
        for i, q in enumerate(result["questions"]):
            if not isinstance(q, dict):
                continue
            text = (q.get("question_text") or "").strip()
            if not text:
                continue
            number = str(q.get("question_number") or f"Q{i + 1}").strip()
            try:
                marks = int(round(float(q.get("marks", 0) or 0)))
            except (TypeError, ValueError):
                marks = 0
            out.append({"question_number": number, "question_text": text, "marks": max(0, marks)})

        if not out:
            raise AIResponseError("no questions could be extracted from the question paper")
        return out

    async def _get_skill(self) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(self.db, "QuestionPaperAgent")
        except Exception:
            return "Extract every question number, text, and marks accurately. Preserve Nepali text."
