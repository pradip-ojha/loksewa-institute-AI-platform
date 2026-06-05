"""Full line-level handwriting extraction from a rendered answer-sheet page.

Demo-quality from the start: every page goes through line-level vision extraction
so the checked PDF can carry precise annotations. The extractor ONLY transcribes
— it does not check, correct, rewrite, or summarize the student's answer.

Coordinates are in the rendered page's pixel space (origin top-left), so they map
directly onto the same PNG the annotator draws on.
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """You are a precise handwriting OCR system for scanned exam answer sheets (Nepali, English, or mixed).

This is page {page_number} of a student's answer sheet. The image is {width} pixels wide and {height} pixels tall (origin at top-left).

Transcribe the handwriting line by line. For EACH line of writing return:
- "id": a unique id for this line on this page, e.g. "p{page_number}_L1", "p{page_number}_L2"
- "text": the exact transcribed text of that line (preserve Devanagari; transcribe formulas, numbers, and table cell contents as written)
- "bbox": [x, y, w, h] integer pixel rectangle tightly enclosing that line
- "qid": the question number this line belongs to IF a question label (e.g. "Q1", "1.", "प्रश्न नं. १") is visible on or just before the line; otherwise null

Known question numbers for this test (use these exact labels when you can identify them): {valid_numbers}

STRICT RULES:
- Transcribe ONLY. Do NOT mark, grade, correct spelling/grammar, rewrite, translate, or summarize.
- If a line is a diagram or unreadable scribble, set text to a short bracketed note like "[diagram]" or "[illegible]" and still give its bbox.
- Do not invent text that is not on the page.

Return ONLY valid JSON in exactly this structure:
{{
  "lines": [
    {{"id": "p{page_number}_L1", "text": "...", "bbox": [120, 430, 620, 32], "qid": "Q1"}}
  ],
  "page_confidence": 0.0
}}"""


class AnswerExtractionAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("reasoning")

    async def extract_page(
        self, *, page_png: bytes, page_number: int, width: int, height: int,
        valid_numbers: list[str], sheet_id: uuid.UUID,
    ) -> dict:
        prompt = EXTRACTION_PROMPT.format(
            page_number=page_number, width=width, height=height,
            valid_numbers=", ".join(valid_numbers) if valid_numbers else "unknown",
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

        if not isinstance(result, dict) or not isinstance(result.get("lines"), list):
            raise AIResponseError(f"answer extraction returned no 'lines' for page {page_number}")

        lines: list[dict] = []
        for ln in result["lines"]:
            if not isinstance(ln, dict):
                continue
            bbox = ln.get("bbox")
            if not (isinstance(bbox, (list, tuple)) and len(bbox) >= 4):
                bbox = None
            lines.append({
                "id": str(ln.get("id") or f"p{page_number}_L{len(lines) + 1}"),
                "text": (ln.get("text") or "").strip(),
                "bbox": [int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])] if bbox else None,
                "qid": (str(ln["qid"]).strip() if ln.get("qid") else None),
                "page": page_number,
            })
        return {"lines": lines, "page_confidence": float(result.get("page_confidence", 0) or 0)}
