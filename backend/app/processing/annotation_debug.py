"""Coordinate debug overlay for the answer-sheet annotation pipeline.

Before trusting the vision locator's geometry (especially with a new vision model),
we need to SEE whether the located points actually land on the right handwriting.
This renders a diagnostic overlay on the page in the same pixel space the checker
used, so a mismatch can be attributed to geometry vs. rendering/style.

Colour legend (drawn on the page):
  • cyan      page corner dots (confirms the pixel space)
  • blue      RAW locator underline points (pre-smoothing, from `original`)
  • orange    RAW target_text_box (from `original`)
  • green     RAW comment_box (from `original`)
  • red       FINAL validated underline path points + line
  • magenta   FINAL comment box (where the comment is actually drawn)
  • purple    section evidence boxes / tick points (positive marking)

Pure drawing — no AI. Consumes the persisted `pdf_annotations.locator_plan` targets.
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw

from app.processing import text_render

CYAN = (0, 170, 200)
BLUE = (40, 90, 230)
ORANGE = (230, 140, 0)
GREEN = (0, 160, 60)
RED = (213, 43, 30)
MAGENTA = (200, 0, 160)
PURPLE = (130, 60, 200)


def _dot(draw: ImageDraw.ImageDraw, x, y, color, r: int = 5) -> None:
    try:
        x, y = float(x), float(y)
    except (TypeError, ValueError):
        return
    draw.ellipse([x - r, y - r, x + r, y + r], fill=color)


def _box(draw: ImageDraw.ImageDraw, raw, color, width: int = 2) -> None:
    if not isinstance(raw, (list, tuple)) or len(raw) < 4:
        return
    try:
        x1, y1, x2, y2 = (float(v) for v in raw[:4])
    except (TypeError, ValueError):
        return
    draw.rectangle([min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)], outline=color, width=width)


def _points(raw) -> list:
    out = []
    if isinstance(raw, (list, tuple)):
        for p in raw:
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                out.append((p[0], p[1]))
    return out


def _label(img: Image.Image, text: str, x: int, y: int, color, size: int) -> None:
    ink = text_render.render_text_rgba(text, size=size, color=color)
    if ink.width <= 1:
        return
    W, H = img.size
    x = max(2, min(int(x), W - ink.width - 2))
    y = max(2, min(int(y), H - ink.height - 2))
    img.alpha_composite(ink, (x, y))


def draw_debug_overlay(page_png: bytes, plans_for_page: list[dict]) -> bytes:
    """Overlay raw + validated locator geometry for one page; return PNG bytes."""
    img = Image.open(io.BytesIO(page_png)).convert("RGBA")
    w, h = img.size
    draw = ImageDraw.Draw(img, "RGBA")
    size = max(13, int(h * 0.012))

    # Page corners — proves the coordinate system / pixel space.
    for (cx, cy) in [(0, 0), (w, 0), (0, h), (w, h)]:
        _dot(draw, min(cx, w - 1), min(cy, h - 1), CYAN, r=7)
    _label(img, f"{w}x{h}", 12, 12, CYAN, size)

    for plan in plans_for_page or []:
        if not isinstance(plan, dict):
            continue
        qnum = str(plan.get("question_number") or "")
        # Per-question plan: validated wrong-text targets + positive section marks.
        for tp in plan.get("targets") or []:
            if not isinstance(tp, dict):
                continue
            original = tp.get("original") if isinstance(tp.get("original"), dict) else None
            if original:
                _box(draw, original.get("target_text_box"), ORANGE)
                _box(draw, original.get("comment_box"), GREEN)
                for path in original.get("underline_paths") or []:
                    for (px, py) in _points((path or {}).get("points")):
                        _dot(draw, px, py, BLUE, r=4)
            for path in tp.get("final_underline_paths") or []:
                pts = _points(path)
                for (px, py) in pts:
                    _dot(draw, px, py, RED, r=4)
                if len(pts) >= 2:
                    draw.line([(float(a), float(b)) for a, b in pts], fill=RED, width=2)
            _box(draw, tp.get("final_comment_box"), MAGENTA)
            anchor = None
            if original and isinstance(original.get("target_text_box"), (list, tuple)):
                tb = original["target_text_box"]; anchor = (tb[0], tb[1] - size - 2)
            elif tp.get("final_comment_box"):
                cb = tp["final_comment_box"]; anchor = (cb[0], cb[1] - size - 2)
            if anchor:
                conf = tp.get("confidence")
                cf = f"{conf:.2f}" if isinstance(conf, (int, float)) else "?"
                _label(img, f"Q{qnum} {tp.get('status') or ''} {cf}", int(anchor[0]), int(anchor[1]), RED, size)

        for sm in plan.get("section_marks") or []:
            if not isinstance(sm, dict):
                continue
            _box(draw, sm.get("evidence_box"), PURPLE)
            pt = sm.get("tick_point")
            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                _dot(draw, pt[0], pt[1], PURPLE, r=5)

    out = io.BytesIO()
    img.convert("RGB").save(out, format="PNG")
    return out.getvalue()
