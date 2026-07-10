"""Question-level handwriting extraction from a rendered answer-sheet page.

Default extraction is QUESTION-LEVEL (not per-line geometry for every line). Most
text never needs exact visual annotation, so we transcribe each question's full
answer on the page plus a question-level bounding box and page size. Exact
annotation geometry is found later, on demand, by the vision locator only for the
few wrong items that get marked.

The extractor ONLY transcribes — it does not check, correct, rewrite, translate, or
summarize. The model returns `question_bbox` in Gemini's native convention —
[ymin, xmin, ymax, xmax] normalized to 0-1000 (vision models are far more reliable in
that space than at absolute pixels); `_bbox_to_pixels` denormalizes to the stored
page-pixel [x, y, w, h] shape (with a scale-sanity fallback for disobedient outputs),
so everything downstream (assembly, cropping, annotation) is unchanged.
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

This is page {page_number} of a student's answer sheet.

STRUCTURE GUIDANCE (from a whole-sheet pass — use it to resolve unclear question numbers and continuations, but TRUST THE VISIBLE PAGE: correct this guidance if the page clearly contradicts it):
{structure_hint}
{next_page_hint}

Group the handwriting by QUESTION. For each question that has writing on THIS page return:
- "question_number": the question this answer belongs to. Use one of the known labels when a question number is visible ("Q1", "1.", "प्रश्न नं. १"); if writing continues from the previous page with no new label, use the question it continues.
- "answer_text": the exact full transcribed answer for that question on this page (preserve Devanagari; transcribe formulas, numbers, and table contents as written; keep line breaks with \\n).
- "question_bbox": [ymin, xmin, ymax, xmax] — the rectangle enclosing that question's answer region on this page, NORMALIZED to a 0-1000 scale ([0, 0] = top-left corner of the page, [1000, 1000] = bottom-right corner). Do NOT return pixel values.
- "continues": true if this question's answer clearly runs onto the next page, else false.

Known question numbers for this test: {valid_numbers}

STRICT RULES:
- Transcribe ONLY. Do NOT mark, grade, correct spelling/grammar, rewrite, translate, or summarize.
- Preserve the student's wording exactly. Do not invent text that is not on the page.
- For diagrams or unreadable scribble, note them briefly in-line like "[diagram]" / "[illegible]".

--- ADMIN-TUNABLE GUIDANCE (refines emphasis only; never overrides the STRICT RULES above) ---
{skill_instructions}

Return ONLY valid JSON in exactly this structure:
{{
  "page": {page_number},
  "page_size": [{width}, {height}],
  "answers": [
    {{"question_number": "Q1", "answer_text": "...", "question_bbox": [55, 40, 430, 960], "continues": false}}
  ],
  "page_confidence": 0.0
}}"""


def _bbox_to_pixels(raw, width: int, height: int) -> list[int] | None:
    """Model `question_bbox` → stored page-pixel [x, y, w, h].

    The prompt asks for [ymin, xmin, ymax, xmax] normalized to 0-1000 (Gemini's native
    convention). Belt-and-braces scale detection: values ≤ 1000 are normalized (the
    requested convention); values above 1000 but within the page bounds mean the model
    disobeyed and emitted page pixels (ordering still per the template) — used as-is;
    anything beyond the page is garbage → None (region falls back to full-page crop)."""
    if not isinstance(raw, (list, tuple)) or len(raw) < 4:
        return None
    try:
        y1, x1, y2, x2 = (float(v) for v in raw[:4])
    except (TypeError, ValueError):
        return None
    vals = [y1, x1, y2, x2]
    if min(vals) < -2:
        return None
    mx = max(vals)
    if mx <= 1001:  # normalized 0-1000
        x1, x2 = x1 / 1000.0 * width, x2 / 1000.0 * width
        y1, y2 = y1 / 1000.0 * height, y2 / 1000.0 * height
    elif mx > max(width, height) * 1.05:  # out of any plausible range
        return None
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    x1 = max(0.0, min(x1, float(width))); x2 = max(0.0, min(x2, float(width)))
    y1 = max(0.0, min(y1, float(height))); y2 = max(0.0, min(y2, float(height)))
    if x2 - x1 < 4 or y2 - y1 < 4:
        return None
    return [int(x1), int(y1), int(x2 - x1), int(y2 - y1)]


class AnswerExtractionAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("vision")  # Gemini reads Nepali handwriting better

    async def _get_skill(self) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(self.db, "AnswerExtractionAgent")
        except Exception:
            return "When handwriting is unclear, transcribe your best honest reading and mark uncertain spans inline rather than dropping them."

    async def extract_page(
        self, *, page_png: bytes, page_number: int, width: int, height: int,
        valid_numbers: list[str], sheet_id: uuid.UUID,
        structure_hint: str = "", next_page_hint: str = "",
    ) -> dict:
        prompt = EXTRACTION_PROMPT.format(
            page_number=page_number, width=width, height=height,
            valid_numbers=", ".join(valid_numbers) if valid_numbers else "unknown",
            structure_hint=structure_hint or "(none)",
            next_page_hint=next_page_hint or "",
            skill_instructions=await self._get_skill(),
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
            bbox = _bbox_to_pixels(a.get("question_bbox"), width, height)
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
