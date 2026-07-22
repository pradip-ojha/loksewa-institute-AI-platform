"""Vision locator: find WHERE wrong text + correct sections sit on the answer page.

Called ONCE per question (per page the question occupies) — not per target — so one
vision call returns: the natural underline PATH for each wrong item, AND a placement
for each correct/partial section's tick + section mark. Ticks/section marks therefore
cost no extra vision calls.

To improve coordinate accuracy on a new vision model, the locator is given a CROP of
the question's answer region (higher relative resolution). The model returns geometry
in its NATIVE convention — [ymin, xmin, ymax, xmax] boxes / [y, x] points normalized
to 0-1000 (vision models are far more reliable in that space than at absolute pixels);
`_yx_box_to_px`/`_yx_point_to_px` denormalize to crop pixels (with a per-item
`coord_mode` scale-sanity detector for disobedient outputs), then `crop_origin` maps
back to full-page pixels.

The locator decides WHERE only — never what is wrong (checker) or how to draw it
(renderer). Output geometry is in the page's pixel space (origin top-left).
"""
import io
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.ai.prompts.shared import EXAM_CONTEXT
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

LOCATOR_PROMPT = EXAM_CONTEXT + """

ROLE: You are a vision locator. You find WHERE specific handwritten text sits on a cropped region
of a scanned answer page so a teacher's red-pen marks can be drawn accurately. You decide WHERE
only — never whether something is right or wrong (already decided) nor how to draw it.

This cropped image shows the answer region for question {question_number}.

COORDINATE SYSTEM (critical): return EVERY box as [ymin, xmin, ymax, xmax] and EVERY point as
[y, x], NORMALIZED to a 0-1000 scale relative to THIS image — [0, 0] is the top-left corner and
[1000, 1000] is the bottom-right corner. Do NOT return pixel values. Example: a box covering the
top-left quarter of the image is [0, 0, 500, 500].

ACCURACY OVER COVERAGE: a precise location or nothing. If you cannot confidently find an item,
return empty geometry and LOW confidence for it — never guess a spot, because a misplaced mark on a
student's sheet is worse than no mark.

(A) WRONG ITEMS TO UNDERLINE — each item has an [index]. Find where that exact text appears and
return the natural underline path UNDER it, echoing back its number as "target_index":
{targets_block}

(B) CORRECT POINTS TO TICK — each item has an [index]. Find where the student's evidence text sits;
return a tight box around it plus a tick point ON the MIDDLE of its FIRST line, echoing "section_index":
{sections_block}

RULES:
- Reference each item ONLY by its [index] ("target_index" / "section_index"). Do NOT repeat the full
  target text, comment, or section label back — just the index. This keeps the response short and
  avoids it being cut off before the JSON is complete.
- Underline path = MULTIPLE ordered points [y, x] following the real (slanted/curved) baseline UNDER the wrong text, left to right (4–8 points, not just two endpoints). If text wraps to a second line, give multiple paths.
- "tick_point" = a single [y, x] ON the FIRST line of the correct evidence, at the line's horizontal MIDDLE — a teacher's ✓ goes on the good text itself, never off in the left margin.
- "evidence_box" / "target_text_box" = a tight [ymin, xmin, ymax, xmax] box around the located text.
- "read_text" = ONLY the FIRST FEW WORDS (at most ~6 words) you actually SEE inside the box you returned — just enough to verify the location, NOT the whole line. Transcribe them as written; if what you see differs from the requested text, still transcribe what you see. KEEP IT SHORT.
- Put comment boxes in margins or blank space — never over the student's writing.
- If you cannot confidently find an item, return empty geometry and a LOW confidence for it (do NOT guess a location).

--- ADMIN-TUNABLE GUIDANCE (refines emphasis only; never overrides the RULES above) ---
{skill_instructions}

Return ONLY valid JSON in exactly this structure (all coordinates normalized 0-1000 as above; keep
every "read_text" to a few words so the response is never truncated):
{{
  "targets": [
    {{"target_index": 0, "target_text_box": [ymin,xmin,ymax,xmax],
      "underline_paths": [{{"points": [[y,x],[y,x],[y,x]]}}],
      "comment_box": [ymin,xmin,ymax,xmax],
      "read_text": "first few words", "confidence": 0.0}}
  ],
  "section_marks": [
    {{"section_index": 0, "evidence_box": [ymin,xmin,ymax,xmax], "tick_point": [y,x],
      "read_text": "first few words", "confidence": 0.0}}
  ]
}}"""


# ── Coordinate conversion: model space → crop-pixel space ─────────────────────────
# The prompt asks for Gemini's NATIVE convention — [ymin,xmin,ymax,xmax] boxes and
# [y,x] points normalized to 0-1000 — because vision models are far more reliable in
# it than at absolute pixels. We convert to crop pixels here (the ONLY place the
# [y,x] ordering is interpreted), then `_shift_box`/`_shift_point` map to page space.
#
# `_detect_coord_mode` classifies each item's raw geometry as a belt-and-braces guard:
#   normalized      — everything ≤ 1000: the requested convention → scale by crop size.
#   pixel_fallback  — values above 1000 but within the crop bounds: the model disobeyed
#                     and emitted crop pixels (ordering still per the template) → use as-is.
#   invalid         — values beyond any plausible range → geometry is zeroed and the
#                     item's confidence forced to 0 so the validator ladder rejects it.
# Note: on a crop smaller than 1000px, true pixel output is indistinguishable from
# normalized and is treated as normalized (the requested convention); the geometry
# validator + ink checks bound the damage of that rare disobedience.

def _collect_coords(*geoms) -> list[float]:
    out: list[float] = []

    def walk(v) -> None:
        if isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            out.append(float(v))
        elif isinstance(v, (list, tuple)):
            for item in v:
                walk(item)
        elif isinstance(v, dict):
            for item in v.values():
                walk(item)

    for g in geoms:
        walk(g)
    return out


def _detect_coord_mode(geoms: list, crop_w: int, crop_h: int) -> str:
    nums = _collect_coords(*geoms)
    if not nums:
        return "empty"
    if min(nums) < -2:
        return "invalid"
    mx = max(nums)
    if mx <= 1001:
        return "normalized"
    if mx <= max(crop_w, crop_h) * 1.05:
        return "pixel_fallback"
    return "invalid"


def _yx_box_to_px(raw, mode: str, cw: int, ch: int):
    """Model [ymin,xmin,ymax,xmax] → crop-pixel [x1,y1,x2,y2] (the internal ordering)."""
    if not isinstance(raw, (list, tuple)) or len(raw) < 4:
        return None
    try:
        y1, x1, y2, x2 = (float(v) for v in raw[:4])
    except (TypeError, ValueError):
        return None
    if mode == "normalized":
        x1, x2 = x1 / 1000.0 * cw, x2 / 1000.0 * cw
        y1, y2 = y1 / 1000.0 * ch, y2 / 1000.0 * ch
    return [x1, y1, x2, y2]


def _yx_point_to_px(raw, mode: str, cw: int, ch: int):
    """Model [y, x] → crop-pixel [x, y] (the internal ordering)."""
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None
    try:
        y, x = float(raw[0]), float(raw[1])
    except (TypeError, ValueError):
        return None
    if mode == "normalized":
        x, y = x / 1000.0 * cw, y / 1000.0 * ch
    return [x, y]


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


def crop_region(page_png: bytes, question_bbox, page_w: int, page_h: int, pad_ratio: float = 0.10):
    """Crop the question's answer region (with padding) from the page PNG.
    Returns (crop_png, crop_origin(ox,oy), crop_w, crop_h). Falls back to the FULL page
    when the bbox is missing/unusable OR looks suspicious (implausibly small/narrow) —
    a wrong extraction bbox must cost locator precision, never correctness."""
    from PIL import Image

    img = Image.open(io.BytesIO(page_png)).convert("RGB")

    def _full_page():
        buf = io.BytesIO(); img.save(buf, format="PNG")
        return buf.getvalue(), (0, 0), img.width, img.height

    if not (isinstance(question_bbox, (list, tuple)) and len(question_bbox) >= 4):
        return _full_page()
    try:
        x, y, w, h = (float(v) for v in question_bbox[:4])
    except (TypeError, ValueError):
        return _full_page()

    # Suspicious-bbox guard: a real answer region spans a good share of the page. A
    # tiny or very narrow bbox is more likely an extraction miss than a real region.
    if (w * h) < 0.04 * (page_w * page_h) or w < 0.25 * page_w:
        return _full_page()

    padx, pady = w * pad_ratio, h * pad_ratio
    x1 = max(0, int(x - padx)); y1 = max(0, int(y - pady))
    x2 = min(page_w, int(x + w + padx)); y2 = min(page_h, int(y + h + pady))
    # Width below ~250px starves the model's pixel budget; a legitimate one-line answer
    # region can be short, so the height floor is only a couple of text lines.
    if x2 - x1 < 250 or y2 - y1 < 120:
        return _full_page()
    crop = img.crop((x1, y1, x2, y2))
    buf = io.BytesIO(); crop.save(buf, format="PNG")
    return buf.getvalue(), (x1, y1), crop.width, crop.height


class AnnotationLocatorAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("vision")  # Gemini locates handwriting better

    async def _get_skill(self) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(self.db, "AnnotationLocatorAgent")
        except Exception:
            return "Bias hard toward precision: a missing mark is far better than a misplaced one. Keep comment boxes clear of the handwriting."

    async def locate_question(
        self, *, crop_png: bytes, crop_origin: tuple[int, int], crop_w: int, crop_h: int,
        page_number: int, question_number: str,
        targets: list[dict], sections: list[dict], sheet_id: uuid.UUID,
    ) -> dict:
        """One vision call for a question's wrong targets + positive sections on one page.
        Returns page-space geometry (crop coords already mapped back via crop_origin)."""
        ox, oy = crop_origin
        # Items are referenced by [index]; the model echoes only the index (not the full
        # text/comment/label), so the response stays short and the authoritative strings
        # are recovered from THESE inputs — the model can never corrupt the comment/target.
        targets_block = "\n".join(
            f'- [{i}] wrong_text: "{(t.get("target_text") or "").replace(chr(34), chr(39))[:300]}"'
            for i, t in enumerate(targets)
        ) or "(none)"
        sections_block = "\n".join(
            f'- [{i}] "{(s.get("section") or "")[:80]}"'
            f' evidence: "{(s.get("evidence_text") or "").replace(chr(34), chr(39))[:250]}"'
            for i, s in enumerate(sections)
        ) or "(none)"

        prompt = LOCATOR_PROMPT.format(
            question_number=question_number,
            targets_block=targets_block, sections_block=sections_block,
            skill_instructions=await self._get_skill(),
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

        # Convert model geometry (normalized 0-1000 [y,x], per-item mode detection) to
        # crop pixels, then map crop-space coordinates back to full-page pixel space.
        # `target_text`/`comment_text` come from the INPUT by "target_index" (authoritative)
        # — the model only supplies geometry + a short `read_text` echo.
        out_targets: list[dict] = []
        for t in result.get("targets") or []:
            if not isinstance(t, dict):
                continue
            idx = t.get("target_index")
            src = targets[idx] if isinstance(idx, int) and 0 <= idx < len(targets) else None
            raw_paths = [(p or {}).get("points") for p in t.get("underline_paths") or []]
            mode = _detect_coord_mode(
                [t.get("target_text_box"), t.get("comment_box"), raw_paths], crop_w, crop_h,
            )
            paths = []
            if mode in ("normalized", "pixel_fallback"):
                for raw_pts in raw_paths:
                    pts = [_yx_point_to_px(pt, mode, crop_w, crop_h) for pt in raw_pts or []]
                    pts = [_shift_point(pt, ox, oy) for pt in pts if pt is not None]
                    if pts:
                        paths.append({"points": pts})
                target_box = _shift_box(_yx_box_to_px(t.get("target_text_box"), mode, crop_w, crop_h), ox, oy)
                comment_box = _shift_box(_yx_box_to_px(t.get("comment_box"), mode, crop_w, crop_h), ox, oy)
                confidence = t.get("confidence")
            else:  # "empty" (nothing to convert) or "invalid" (out-of-range garbage)
                target_box = comment_box = None
                confidence = 0.0 if mode == "invalid" else t.get("confidence")
            out_targets.append({
                "page_number": page_number,
                "question_number": question_number,
                # authoritative from input; fall back to any model echo if index missing
                "target_text": (src.get("target_text") if src else t.get("target_text")),
                "target_text_box": target_box,
                "underline_paths": paths,
                "comment_box": comment_box,
                "comment_text": (src.get("comment_text") if src else t.get("comment_text")),
                "annotation_action": "underline_with_comment",
                "confidence": confidence,
                "coord_mode": mode,
                "read_text": t.get("read_text"),
            })

        # Sections are referenced by "section_index"; recover the authoritative label +
        # evidence_text from the INPUT (the validator matches the model's read_text against
        # it). Fall back to a string-echo match if a model still returns "section" by name.
        by_label = {(s.get("section") or "").strip(): s for s in sections}
        out_sections: list[dict] = []
        for s in result.get("section_marks") or []:
            if not isinstance(s, dict):
                continue
            idx = s.get("section_index")
            src = sections[idx] if isinstance(idx, int) and 0 <= idx < len(sections) else None
            if src is None:
                src = by_label.get((s.get("section") or "").strip())
            mode = _detect_coord_mode([s.get("evidence_box"), s.get("tick_point")], crop_w, crop_h)
            if mode in ("normalized", "pixel_fallback"):
                evidence_box = _shift_box(_yx_box_to_px(s.get("evidence_box"), mode, crop_w, crop_h), ox, oy)
                tick_point = _shift_point(_yx_point_to_px(s.get("tick_point"), mode, crop_w, crop_h), ox, oy)
                confidence = s.get("confidence")
            else:
                evidence_box = tick_point = None
                confidence = 0.0 if mode == "invalid" else s.get("confidence")
            out_sections.append({
                "section": (src.get("section") if src else s.get("section")),
                "evidence_box": evidence_box,
                "tick_point": tick_point,
                "confidence": confidence,
                "coord_mode": mode,
                "read_text": s.get("read_text"),
                "evidence_text": (src.get("evidence_text") if src else "") or "",
            })

        return {"page_number": page_number, "question_number": question_number,
                "targets": out_targets, "section_marks": out_sections,
                "crop": {"origin": [ox, oy], "size": [crop_w, crop_h]}}
