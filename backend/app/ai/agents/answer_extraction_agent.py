"""Question-level handwriting extraction from a rendered answer-sheet page.

Default extraction is QUESTION-LEVEL (not per-line geometry for every line). Most
text never needs exact visual annotation, so we transcribe each question's full
answer on the page plus a question-level bounding box and page size. Exact
annotation geometry is found later, on demand, by the vision locator only for the
few wrong items that get marked.

The extractor ONLY transcribes — it does not check, correct, rewrite, translate, or
summarize. Coordinates are in the rendered page's pixel space (origin top-left).
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.ai.prompts.shared import EXAM_CONTEXT
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = EXAM_CONTEXT + """

ROLE: You are a precise handwriting OCR system for scanned Loksewa exam answer sheets. Students
write fast, in Nepali (Devanagari), English, or a mix, with corrections and varied handwriting.
Your transcription is the only text the checker will see, so capture it faithfully and completely.

This is page {page_number} of a student's answer sheet. The image is {width} pixels wide and {height} pixels tall (origin at top-left).

STRUCTURE GUIDANCE (from a whole-sheet pass — use it to resolve unclear question numbers and continuations, but TRUST THE VISIBLE PAGE: correct this guidance if the page clearly contradicts it):
{structure_hint}
{prev_page_tail}
{next_page_hint}

Group the handwriting by QUESTION. For each question that has writing on THIS page return:
- "question_number": the question this answer belongs to. Use one of the known labels when a question number is visible ("Q1", "1.", "प्रश्न नं. १"); if writing continues from the previous page with no new label, use the question it continues.
- "answer_text": the exact full transcribed answer for that question on this page (preserve Devanagari; transcribe formulas, numbers, and table contents as written; keep line breaks with \\n).
- "question_bbox": [x, y, w, h] integer pixel rectangle enclosing that question's answer region on this page.
- "continues": true if this question's answer clearly runs onto the next page, else false.

Known question numbers for this test: {valid_numbers}

STRICT RULES:
- Transcribe ONLY. Do NOT mark, grade, correct spelling/grammar, rewrite, translate, or summarize.
- Preserve the student's wording exactly. Do not invent text that is not on the page.
- For diagrams or unreadable scribble, note them briefly in-line like "[diagram]" / "[illegible]".

Return ONLY valid JSON in exactly this structure:
{{
  "page": {page_number},
  "page_size": [{width}, {height}],
  "answers": [
    {{"question_number": "Q1", "answer_text": "...", "question_bbox": [80, 120, 1000, 640], "continues": false}}
  ],
  "page_confidence": 0.0
}}"""


class AnswerExtractionAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("vision")  # Gemini reads Nepali handwriting better

    async def extract_page(
        self, *, page_png: bytes, page_number: int, width: int, height: int,
        valid_numbers: list[str], sheet_id: uuid.UUID,
        structure_hint: str = "", prev_page_tail: str = "", next_page_hint: str = "",
    ) -> dict:
        prompt = EXTRACTION_PROMPT.format(
            page_number=page_number, width=width, height=height,
            valid_numbers=", ".join(valid_numbers) if valid_numbers else "unknown",
            structure_hint=structure_hint or "(none)",
            prev_page_tail=prev_page_tail or "",
            next_page_hint=next_page_hint or "",
        )
        audit_ctx = {
            "db": self.db,
            "agent_type": "AnswerExtractionAgent",
            "task_type": "answer_sheet_extraction",
            "entity_type": "student_answer_sheet",
            "entity_id": sheet_id,
        }
        try:
            result = await self.provider.generate_with_image(
                prompt, page_png, schema={}, audit_ctx=audit_ctx,
            )
        except Exception as exc:
            raise RuntimeError(f"Answer extraction failed on page {page_number}: {exc}") from exc

        if not isinstance(result, dict) or not isinstance(result.get("answers"), list):
            raise AIResponseError(f"answer extraction returned no 'answers' for page {page_number}")

        answers: list[dict] = []
        for a in result["answers"]:
            if not isinstance(a, dict):
                continue
            bbox = a.get("question_bbox")
            if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
                try:
                    bbox = [int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])]
                except (TypeError, ValueError):
                    bbox = None
            else:
                bbox = None
            qnum = a.get("question_number")
            answers.append({
                "question_number": (str(qnum).strip() if qnum else None),
                "answer_text": (a.get("answer_text") or "").strip(),
                "question_bbox": bbox,
                "continues": bool(a.get("continues")),
                "page": page_number,
            })
        return {
            "page": page_number,
            "page_size": [width, height],
            "answers": answers,
            "page_confidence": float(result.get("page_confidence", 0) or 0),
        }
