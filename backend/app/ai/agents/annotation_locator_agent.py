"""Vision locator: find WHERE wrong text + correct sections sit on the answer page.

Called ONCE per question (per page the question occupies) — not per target — so one
vision call returns: the natural underline PATH for each wrong item, AND a placement
for each correct/partial section's tick + section mark. Ticks/section marks therefore
cost no extra vision calls.

To improve coordinate accuracy on a new vision model, the locator is given a CROP of
the question's answer region (higher relative resolution); returned crop-space
coordinates are mapped back to full-page pixels here via `crop_origin`.

The locator decides WHERE only — never what is wrong (checker) or how to draw it
(renderer). Geometry is in the page's pixel space (origin top-left).
"""
import io
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

LOCATOR_PROMPT = """You locate handwritten text on a CROPPED region of a scanned exam answer page, so a teacher's marks can be drawn.

This cropped image is {width} pixels wide and {height} pixels tall (origin top-left). All coordinates you return MUST be in THIS cropped image's pixel space. It shows the answer region for question {question_number}.

(A) WRONG ITEMS TO UNDERLINE — for each, find where that exact text appears and return the natural underline path UNDER it:
{targets_block}

(B) CORRECT POINTS TO TICK — for each, find where the student's evidence text sits and return a tight box around that text plus a tick point just left of its FIRST line:
{sections_block}

RULES:
- Underline path = MULTIPLE ordered points [x, y] following the real (slanted/curved) baseline UNDER the wrong text, left to right (4–8 points, not just two endpoints). If text wraps to a second line, give multiple paths.
- "tick_point" = a single [x, y] in blank space just left of the FIRST line of the correct evidence (where a ✓ goes), NOT on top of the writing.
- "evidence_box" / "target_text_box" = a tight box around the located text.
- Put comment boxes in margins or blank space — never over the student's writing.
- If you cannot confidently find an item, return empty geometry and a LOW confidence for it (do NOT guess a location).

Return ONLY valid JSON in exactly this structure:
{{
  "targets": [
    {{"target_text": "...", "target_text_box": [x1,y1,x2,y2],
      "underline_paths": [{{"points": [[x,y],[x,y],[x,y]]}}],
      "comment_box": [x1,y1,x2,y2], "comment_text": "...", "confidence": 0.0}}
  ],
  "section_marks": [
    {{"section": "...", "evidence_box": [x1,y1,x2,y2], "tick_point": [x,y], "confidence": 0.0}}
  ]
}}"""


def _shift_box(box, ox, oy):
    if not isinstance(box, (list, tuple)) or len(box) < 4:
        return box
    try:
        return [float(box[0]) + ox, float(box[1]) + oy, float(box[2]) + ox, float(box[3]) + oy]
    except (TypeError, ValueError):
        return box


def _shift_point(pt, ox, oy):
    if not isinstance(pt, (list, tuple)) or len(pt) < 2:
        return pt
    try:
        return [float(pt[0]) + ox, float(pt[1]) + oy]
    except (TypeError, ValueError):
        return pt


def crop_region(page_png: bytes, question_bbox, page_w: int, page_h: int, pad_ratio: float = 0.06):
    """Crop the question's answer region (with padding) from the page PNG.
    Returns (crop_png, crop_origin(ox,oy), crop_w, crop_h). Falls back to the full page
    when the bbox is missing/unusable."""
    from PIL import Image

    img = Image.open(io.BytesIO(page_png)).convert("RGB")
    if not (isinstance(question_bbox, (list, tuple)) and len(question_bbox) >= 4):
        buf = io.BytesIO(); img.save(buf, format="PNG")
        return buf.getvalue(), (0, 0), img.width, img.height
    try:
        x, y, w, h = (float(v) for v in question_bbox[:4])
    except (TypeError, ValueError):
        buf = io.BytesIO(); img.save(buf, format="PNG")
        return buf.getvalue(), (0, 0), img.width, img.height

    padx, pady = w * pad_ratio, h * pad_ratio
    x1 = max(0, int(x - padx)); y1 = max(0, int(y - pady))
    x2 = min(page_w, int(x + w + padx)); y2 = min(page_h, int(y + h + pady))
    if x2 - x1 < 8 or y2 - y1 < 8:
        buf = io.BytesIO(); img.save(buf, format="PNG")
        return buf.getvalue(), (0, 0), img.width, img.height
    crop = img.crop((x1, y1, x2, y2))
    buf = io.BytesIO(); crop.save(buf, format="PNG")
    return buf.getvalue(), (x1, y1), crop.width, crop.height


class AnnotationLocatorAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("vision")  # Gemini locates handwriting better

    async def locate_question(
        self, *, crop_png: bytes, crop_origin: tuple[int, int], crop_w: int, crop_h: int,
        page_number: int, question_number: str,
        targets: list[dict], sections: list[dict], sheet_id: uuid.UUID,
    ) -> dict:
        """One vision call for a question's wrong targets + positive sections on one page.
        Returns page-space geometry (crop coords already mapped back via crop_origin)."""
        ox, oy = crop_origin
        targets_block = "\n".join(
            f'- target_text: "{(t.get("target_text") or "").replace(chr(34), chr(39))[:300]}"'
            f' | comment: "{(t.get("comment_text") or "").replace(chr(34), chr(39))[:200]}"'
            for t in targets
        ) or "(none)"
        sections_block = "\n".join(
            f'- section: "{(s.get("section") or "")[:120]}"'
            f' | evidence_text: "{(s.get("evidence_text") or "").replace(chr(34), chr(39))[:300]}"'
            for s in sections
        ) or "(none)"

        prompt = LOCATOR_PROMPT.format(
            width=crop_w, height=crop_h, question_number=question_number,
            targets_block=targets_block, sections_block=sections_block,
        )
        audit_ctx = {
            "db": self.db,
            "agent_type": "AnnotationLocatorAgent",
            "task_type": "annotation_location",
            "entity_type": "student_answer_sheet",
            "entity_id": sheet_id,
        }
        try:
            result = await self.provider.generate_with_image(prompt, crop_png, schema={}, audit_ctx=audit_ctx)
        except Exception as exc:
            raise RuntimeError(f"Annotation location failed on page {page_number}: {exc}") from exc
        if not isinstance(result, dict):
            raise AIResponseError("annotation locator did not return an object")

        # Map crop-space coordinates back to full-page pixel space.
        out_targets: list[dict] = []
        for t in result.get("targets") or []:
            if not isinstance(t, dict):
                continue
            paths = []
            for p in t.get("underline_paths") or []:
                pts = [_shift_point(pt, ox, oy) for pt in (p or {}).get("points") or []]
                pts = [pt for pt in pts if isinstance(pt, list)]
                if pts:
                    paths.append({"points": pts})
            out_targets.append({
                "page_number": page_number,
                "question_number": question_number,
                "target_text": t.get("target_text"),
                "target_text_box": _shift_box(t.get("target_text_box"), ox, oy),
                "underline_paths": paths,
                "comment_box": _shift_box(t.get("comment_box"), ox, oy),
                "comment_text": t.get("comment_text"),
                "annotation_action": "underline_with_comment",
                "confidence": t.get("confidence"),
            })

        out_sections: list[dict] = []
        for s in result.get("section_marks") or []:
            if not isinstance(s, dict):
                continue
            out_sections.append({
                "section": s.get("section"),
                "evidence_box": _shift_box(s.get("evidence_box"), ox, oy),
                "tick_point": _shift_point(s.get("tick_point"), ox, oy),
                "confidence": s.get("confidence"),
            })

        return {"page_number": page_number, "question_number": question_number,
                "targets": out_targets, "section_marks": out_sections}
