"""Vision locator: find WHERE a wrong piece of text sits on the answer-sheet page.

Called per reviewed annotation target only (not for every line). Given the page
image (or cropped question region) and the exact wrong `target_text`, it returns the
natural underline PATH — multiple ordered points along the handwriting baseline, not
two bbox endpoints — plus a safe nearby comment box. This is what lets the renderer
draw a curved, hand-like underline instead of a rigid straight line.

The locator decides WHERE only — never what is wrong (checker) or how to draw it
(renderer). Geometry is in the page's pixel space (origin top-left).
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

LOCATOR_PROMPT = """You locate a specific piece of WRONG handwritten text on a scanned exam answer page, so it can be underlined.

This page is {width} pixels wide and {height} pixels tall (origin top-left). It belongs to question {question_number}.
{region_hint}

TEXT TO LOCATE (the exact wrong written item — it may be a phrase, a formula, a number, or one line):
"{target_text}"

CORRECTION COMMENT that will be placed near it:
"{comment_text}"

YOUR TASK:
- Find where that text appears in the handwriting.
- Return the natural underline path as MULTIPLE ordered points [x, y] that follow the baseline UNDER the text (left to right). Handwriting is slanted/curved, so give 4–8 points that trace the real baseline, NOT just two endpoints.
- If the text wraps across two written lines, return multiple paths (one per line).
- Return a tight box around the located text, and a SAFE comment box in nearby blank space (right margin or just above/below) that does NOT overlap the student's writing.
- If you cannot confidently find the text, return an empty "underline_paths" and a low confidence.

Return ONLY valid JSON in exactly this structure:
{{
  "page_number": {page_number},
  "question_number": "{question_number}",
  "target_text": "{target_text}",
  "target_text_box": [x1, y1, x2, y2],
  "underline_paths": [{{"points": [[x, y], [x, y], [x, y]]}}],
  "comment_box": [x1, y1, x2, y2],
  "comment_text": "{comment_text}",
  "annotation_action": "{annotation_action}",
  "confidence": 0.0
}}"""


class AnnotationLocatorAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("reasoning")

    async def locate(
        self, *, page_png: bytes, page_number: int, width: int, height: int,
        question_number: str, target_text: str, comment_text: str,
        annotation_action: str, question_bbox: list | None, sheet_id: uuid.UUID,
    ) -> dict:
        region_hint = (
            f"The question's answer region is approximately the pixel rectangle [x,y,w,h] = {question_bbox}."
            if question_bbox else "The question's answer region is not pre-known; scan the whole page."
        )
        prompt = LOCATOR_PROMPT.format(
            width=width, height=height, page_number=page_number,
            question_number=question_number,
            target_text=(target_text or "").replace('"', "'")[:400],
            comment_text=(comment_text or "").replace('"', "'")[:300],
            annotation_action=annotation_action or "underline_with_comment",
            region_hint=region_hint,
        )
        audit_ctx = {
            "db": self.db,
            "agent_type": "AnnotationLocatorAgent",
            "task_type": "annotation_location",
            "entity_type": "student_answer_sheet",
            "entity_id": sheet_id,
        }
        try:
            result = await self.provider.generate_with_image(
                prompt, page_png, schema={}, audit_ctx=audit_ctx,
            )
        except Exception as exc:
            raise RuntimeError(f"Annotation location failed on page {page_number}: {exc}") from exc
        if not isinstance(result, dict):
            raise AIResponseError("annotation locator did not return an object")
        # Normalize / guarantee keys the validator depends on.
        result.setdefault("page_number", page_number)
        result.setdefault("question_number", question_number)
        result.setdefault("target_text", target_text)
        result.setdefault("comment_text", comment_text)
        result.setdefault("annotation_action", annotation_action)
        if not isinstance(result.get("underline_paths"), list):
            result["underline_paths"] = []
        return result
