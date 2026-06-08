"""Validate + smooth the vision locator's annotation geometry before drawing.

Pure Python, no AI. The locator (a vision model) suggests WHERE wrong text sits and
where a comment can go; we never trust that geometry blindly. This module decides
WHETHER it is safe to draw, smooths slightly-noisy underline paths, and falls back
to safer marks when the geometry is unreliable — so the checked PDF never gets a red
line drawn at a random or low-confidence location.

Behaviour ladder (per target):
  • valid path                  → use it (status "ok")
  • valid but noisy path        → smooth it (status "smoothed")
  • bad path, good text box     → short soft underline along the box baseline ("soft_mark")
  • both unreliable             → no exact mark; question-area feedback only ("feedback_only")
  • confidence too low / garbage→ "rejected"

Output is consumed by `annotation.py` (the renderer) and persisted in
`pdf_annotations.locator_plan` for audit.
"""
from __future__ import annotations

CONFIDENCE_MIN = 0.45          # below this we do not draw an exact underline
SOFT_MARK_CONF_MIN = 0.30      # below this we don't even soft-mark
TICK_CONF_MIN = 0.55           # below this we do not place a positive tick (skip it)
# A path's horizontal span should be roughly the width of the target text.
PATH_SPAN_MIN_RATIO = 0.35
PATH_SPAN_MAX_RATIO = 2.4
# How far below/above the target box bottom an underline point may sit (as a ratio of box height).
VERTICAL_BAND_RATIO = 1.6


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _box4(raw, w: int, h: int) -> list[int] | None:
    """Coerce [x1,y1,x2,y2] into an ordered, in-bounds box. None if unusable."""
    if not isinstance(raw, (list, tuple)) or len(raw) < 4:
        return None
    vals = [_num(x) for x in raw[:4]]
    if any(v is None for v in vals):
        return None
    x1, y1, x2, y2 = vals
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    x1 = _clamp(x1, 0, w); x2 = _clamp(x2, 0, w)
    y1 = _clamp(y1, 0, h); y2 = _clamp(y2, 0, h)
    if x2 - x1 < 4 or y2 - y1 < 2:
        return None
    return [int(x1), int(y1), int(x2), int(y2)]


def _clean_points(raw, w: int, h: int) -> list[list[float]]:
    pts: list[list[float]] = []
    if not isinstance(raw, (list, tuple)):
        return pts
    for p in raw:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            continue
        x, y = _num(p[0]), _num(p[1])
        if x is None or y is None:
            continue
        pts.append([_clamp(x, 0, w), _clamp(y, 0, h)])
    return pts


def _smooth(points: list[list[float]]) -> list[list[float]]:
    """Sort left-to-right, drop near-duplicates, and moving-average the y values so a
    jittery baseline reads as a natural, gently-waving line."""
    if len(points) < 2:
        return points
    pts = sorted(points, key=lambda p: p[0])
    dedup: list[list[float]] = []
    for p in pts:
        if dedup and abs(p[0] - dedup[-1][0]) < 3:
            continue
        dedup.append(p)
    if len(dedup) < 2:
        return points
    out: list[list[float]] = []
    for i, p in enumerate(dedup):
        lo, hi = max(0, i - 1), min(len(dedup), i + 2)
        ys = [q[1] for q in dedup[lo:hi]]
        out.append([p[0], sum(ys) / len(ys)])
    return out


def _path_span(points: list[list[float]]) -> float:
    xs = [p[0] for p in points]
    return (max(xs) - min(xs)) if xs else 0.0


def _path_near_box(points: list[list[float]], box: list[int]) -> bool:
    """Underline points should sit around the box's baseline, not far from it."""
    x1, y1, x2, y2 = box
    band = max(8.0, (y2 - y1) * VERTICAL_BAND_RATIO)
    baseline = y2
    inside = 0
    for px, py in points:
        if (x1 - 30) <= px <= (x2 + 30) and (baseline - band) <= py <= (baseline + band):
            inside += 1
    return inside >= max(2, len(points) // 2)


def _boxes_overlap(a: list[int], b: list[int]) -> float:
    """Intersection area / area(a). 0 when disjoint."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    area_a = max(1, (ax2 - ax1) * (ay2 - ay1))
    return inter / area_a


def _safe_comment_box(raw, target_box: list[int] | None, page_size, comment_text: str) -> list[int] | None:
    """Validate the suggested comment box; relocate to the right margin (or just below
    the target) if it would sit on top of the student's writing. None → let the
    renderer fall back to a question-area feedback box."""
    w, h = page_size
    box = _box4(raw, w, h)
    cw = int(_clamp(len(comment_text or "") * 9 + 24, 140, w * 0.32))
    ch = 64
    if box and (not target_box or _boxes_overlap(box, target_box) < 0.15):
        return box
    # Relocate: prefer the right margin aligned with the target's vertical position.
    if target_box:
        tx1, ty1, tx2, ty2 = target_box
        if (w - tx2) > (cw + 24):                       # room on the right
            x1 = min(w - cw - 8, tx2 + 16)
            y1 = int(_clamp(ty1, 4, h - ch - 4))
            return [x1, y1, x1 + cw, y1 + ch]
        if (h - ty2) > (ch + 16):                        # room just below
            x1 = int(_clamp(tx1, 4, w - cw - 4))
            y1 = min(h - ch - 4, ty2 + 12)
            return [x1, y1, x1 + cw, y1 + ch]
    return None


def validate_and_smooth(locator_result: dict, page_size, question_bbox=None) -> dict:
    """Return a validated annotation plan for one target. See module docstring for the
    behaviour ladder. `page_size` = (width, height); `question_bbox` = [x,y,w,h] or None."""
    w, h = int(page_size[0]), int(page_size[1])
    conf = _num(locator_result.get("confidence")) or 0.0
    target_box = _box4(locator_result.get("target_text_box"), w, h)
    comment_text = (locator_result.get("comment_text") or "").strip()

    plan: dict = {
        "page_number": locator_result.get("page_number"),
        "question_number": locator_result.get("question_number"),
        "target_text": locator_result.get("target_text"),
        "comment_text": comment_text,
        "annotation_action": locator_result.get("annotation_action") or "underline_with_comment",
        "confidence": conf,
        "status": "rejected",
        "fallback_type": None,
        "final_underline_paths": [],
        "final_comment_box": None,
        "reason": "",
        "original": locator_result,
    }

    comment_box = _safe_comment_box(
        locator_result.get("comment_box"), target_box, (w, h), comment_text,
    )

    # Too little confidence to draw anything precise.
    if conf < SOFT_MARK_CONF_MIN:
        plan["status"] = "feedback_only"
        plan["fallback_type"] = "feedback_only"
        plan["final_comment_box"] = comment_box
        plan["reason"] = f"confidence {conf:.2f} below soft-mark threshold"
        return plan

    # Validate each suggested underline path.
    good_paths: list[list[list[float]]] = []
    target_w = (target_box[2] - target_box[0]) if target_box else None
    for path in locator_result.get("underline_paths") or []:
        pts = _clean_points((path or {}).get("points"), w, h)
        if len(pts) < 2:
            continue
        pts = _smooth(pts)
        span = _path_span(pts)
        if target_w:
            if span < target_w * PATH_SPAN_MIN_RATIO or span > target_w * PATH_SPAN_MAX_RATIO:
                continue
            if not _path_near_box(pts, target_box):
                continue
        good_paths.append(pts)

    if good_paths and conf >= CONFIDENCE_MIN:
        smoothed = any(True for _ in good_paths)  # all paths already smoothed
        plan["final_underline_paths"] = good_paths
        plan["final_comment_box"] = comment_box
        plan["status"] = "smoothed" if smoothed else "ok"
        plan["reason"] = "valid underline path(s)"
        return plan

    # No usable path but we know the text box → safer short soft underline on its baseline.
    if target_box and conf >= SOFT_MARK_CONF_MIN:
        x1, y1, x2, y2 = target_box
        baseline = y2 + max(3, int((y2 - y1) * 0.15))
        soft = [[x1, baseline], [(x1 + x2) / 2, baseline], [x2, baseline]]
        plan["final_underline_paths"] = [soft]
        plan["final_comment_box"] = comment_box
        plan["status"] = "soft_mark"
        plan["fallback_type"] = "soft_mark"
        plan["reason"] = "path unreliable; soft mark along target box baseline"
        return plan

    # Nothing reliable → feedback only.
    plan["status"] = "feedback_only"
    plan["fallback_type"] = "feedback_only"
    plan["final_comment_box"] = comment_box
    plan["reason"] = "no reliable geometry"
    return plan


def _validate_section(sec: dict, w: int, h: int) -> dict | None:
    """Place one positive section's tick next to the located correct line.

    A tick is drawn ONLY when the locator confidently found the student's evidence
    (a valid `evidence_box` and confidence ≥ `TICK_CONF_MIN`). Otherwise we return
    None so the caller skips it — we never dump a tick into a blank margin at a guessed
    spot. The tick sits just left of the evidence's first line, like a teacher's pen
    tick beside a good point."""
    conf = _num(sec.get("confidence")) or 0.0
    evid = _box4(sec.get("evidence_box"), w, h)
    if not evid or conf < TICK_CONF_MIN:
        return None

    x1, y1, x2, y2 = evid
    # Bias the tick toward the FIRST line of the evidence (tall multi-line boxes), so it
    # reads as a tick on the correct point rather than mid-paragraph.
    line_est = min(max(24.0, h * 0.022), float(y2 - y1))
    yc = _clamp(y1 + line_est / 2, 8, h - 8)
    # Place the tick over the MIDDLE of the located line (on the text), not in the margin.
    cx = (x1 + x2) / 2
    tick_pt = [_clamp(cx, 6, w - 8), yc]
    return {
        "section": sec.get("section"),
        "tick_point": tick_pt,
        "evidence_box": evid,
        "confidence": conf,
        "source": "evidence",
    }


def validate_question_plan(locator_result: dict, page_size, question_bbox=None) -> dict:
    """Validate a per-question locator result (wrong targets + positive sections).
    Underlines use the safety ladder in `validate_and_smooth`; positive sections become
    a tick ONLY where the evidence was confidently located (skip-on-miss — no margin
    dumping). `question_bbox` is [x, y, w, h] or None."""
    w, h = int(page_size[0]), int(page_size[1])

    targets = [
        validate_and_smooth(t, (w, h), question_bbox)
        for t in (locator_result.get("targets") or []) if isinstance(t, dict)
    ]
    raw_sections = [s for s in (locator_result.get("section_marks") or []) if isinstance(s, dict)]
    sections = [
        v for v in (_validate_section(s, w, h) for s in raw_sections) if v is not None
    ]
    return {
        "page_number": locator_result.get("page_number"),
        "question_number": locator_result.get("question_number"),
        "targets": targets,
        "section_marks": sections,
    }
