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

from app.processing.text_match import similarity as _text_similarity

CONFIDENCE_MIN = 0.45          # below this we do not draw an exact underline
SOFT_MARK_CONF_MIN = 0.30      # below this we don't even soft-mark
TICK_CONF_MIN = 0.55           # below this we do not place a positive tick (skip it)
# A path's horizontal span should be roughly the width of the target text.
PATH_SPAN_MIN_RATIO = 0.35
PATH_SPAN_MAX_RATIO = 2.4
# How far below/above the target box bottom an underline point may sit (as a ratio of box height).
VERTICAL_BAND_RATIO = 1.6
# Ground-truth guards (deterministic, no AI):
# minimum dark-ink pixel fraction in the strip an underline claims to sit under — an
# underline through blank paper is a location miss, not a style choice.
INK_MIN_FRACTION = 0.015
# minimum fuzzy similarity between the locator's `read_text` echo (what it saw inside
# the box it returned) and the text it was asked to find; below this the location is
# treated as unverified and the confidence is capped under CONFIDENCE_MIN.
ECHO_MATCH_MIN = 0.35


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


# ── Ink-presence ground-truth checks ──────────────────────────────────────────────
# The locator's geometry is only trusted where the page actually has ink: an underline
# whose claimed text strip is blank paper, or an evidence box with no writing in it, is
# a location miss and must not be drawn. Otsu thresholding adapts to faint pencil.

def build_ink_mask(page_png: bytes):
    """Binary ink mask (ink=255) from a page PNG, or None when undecodable / cv2
    unavailable. Callers treat None as 'skip ink checks'."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    try:
        arr = np.frombuffer(page_png, dtype=np.uint8)
        gray = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        if gray is None:
            return None
        _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        return mask
    except Exception:
        return None


def _ink_fraction_above_path(mask, points: list[list[float]], line_h: float) -> float:
    """Dark-pixel fraction in the strip a proposed underline claims to sit UNDER
    (from the path baseline up ~1.4 line heights, over the path's x-span)."""
    h, w = mask.shape[:2]
    xs = [p[0] for p in points]
    x1 = int(_clamp(min(xs), 0, w)); x2 = int(_clamp(max(xs), 0, w))
    if x2 - x1 < 4:
        return 0.0
    baseline = sum(p[1] for p in points) / len(points)
    y2 = int(_clamp(baseline - 2, 0, h))
    y1 = int(_clamp(baseline - 1.4 * max(8.0, line_h), 0, h))
    if y2 - y1 < 2:
        return 0.0
    strip = mask[y1:y2, x1:x2]
    return float((strip > 0).mean()) if strip.size else 0.0


def _ink_fraction_in_box(mask, box: list[int]) -> float:
    h, w = mask.shape[:2]
    x1 = int(_clamp(box[0], 0, w)); x2 = int(_clamp(box[2], 0, w))
    y1 = int(_clamp(box[1], 0, h)); y2 = int(_clamp(box[3], 0, h))
    if x2 - x1 < 2 or y2 - y1 < 2:
        return 0.0
    region = mask[y1:y2, x1:x2]
    return float((region > 0).mean()) if region.size else 0.0


def _comment_dims(comment_text: str, w: int, h: int) -> tuple[int, int]:
    """Estimated (width, height) of the rendered comment using real HarfBuzz shaping —
    mirrors the renderer's comment font size (`base*1.5`, bold) and 4-line wrap so the box
    actually fits what `annotation._draw_note_card` will draw."""
    size = int(max(16, int(h * 0.016)) * 1.5)  # must match annotation.draw_annotations `comment_size`
    try:
        from app.processing.text_render import measure_text
        line_w, line_h = measure_text(comment_text or "", size, bold=True)
    except Exception:
        line_w, line_h = len(comment_text or "") * 13, int(size * 1.35)
    max_w = int(w * 0.40)
    cw = int(_clamp(line_w + 24, 140, max_w))
    lines = min(4, max(1, -(-line_w // max(1, cw - 24))))  # ceil-div over usable width
    ch = int(line_h * 1.15) * lines + 10
    return cw, ch


def _safe_comment_box(raw, target_box: list[int] | None, page_size, comment_text: str,
                      question_bbox=None) -> list[int] | None:
    """Validate the suggested comment box; relocate to the right margin (or just below
    the target, or — last resort — the bottom of the question region) if it would sit
    on top of the student's writing. Returns None only for an empty comment."""
    if not (comment_text or "").strip():
        return None
    w, h = page_size
    box = _box4(raw, w, h)
    cw, ch = _comment_dims(comment_text, w, h)
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
    # Last resort: anchor at the bottom of the question's answer region (question-area
    # feedback, CLAUDE.md §12) — a comment must never silently vanish.
    if isinstance(question_bbox, (list, tuple)) and len(question_bbox) >= 4:
        try:
            qx, qy, qw, qh = (float(v) for v in question_bbox[:4])
        except (TypeError, ValueError):
            qx, qy, qw, qh = 8.0, 8.0, float(w) - 16, float(h) - 16
    else:
        qx, qy, qw, qh = 8.0, 8.0, float(w) - 16, float(h) - 16
    x1 = int(_clamp(qx + 8, 4, max(4, w - cw - 4)))
    y1 = int(_clamp(qy + qh + 8, 4, max(4, h - ch - 4)))
    cand = [x1, y1, x1 + cw, y1 + ch]
    if target_box and _boxes_overlap(cand, target_box) >= 0.15:
        y1 = int(_clamp(target_box[3] + 8, 4, max(4, h - ch - 4)))
        cand = [x1, y1, x1 + cw, y1 + ch]
    return cand


def validate_and_smooth(locator_result: dict, page_size, question_bbox=None, ink_mask=None) -> dict:
    """Return a validated annotation plan for one target. See module docstring for the
    behaviour ladder. `page_size` = (width, height); `question_bbox` = [x,y,w,h] or None;
    `ink_mask` (optional, from `build_ink_mask`) enables the ink-presence ground-truth
    checks — None skips them."""
    w, h = int(page_size[0]), int(page_size[1])
    conf = _num(locator_result.get("confidence")) or 0.0
    target_box = _box4(locator_result.get("target_text_box"), w, h)
    comment_text = (locator_result.get("comment_text") or "").strip()

    # Echo verification: the locator transcribed what it saw inside the box it returned
    # (`read_text`). If that doesn't resemble the text it was asked to find, the
    # location is unverified — cap confidence below the exact-underline threshold so
    # only the safer rungs of the ladder remain reachable.
    match_score = None
    read_text = (locator_result.get("read_text") or "").strip()
    target_text = (locator_result.get("target_text") or "").strip()
    echo_note = ""
    if read_text and target_text:
        match_score = round(_text_similarity(read_text, target_text), 3)
        if match_score < ECHO_MATCH_MIN:
            conf = min(conf, CONFIDENCE_MIN - 0.01)
            echo_note = f"; read_text echo mismatch (score {match_score:.2f})"

    plan: dict = {
        "page_number": locator_result.get("page_number"),
        "question_number": locator_result.get("question_number"),
        "target_text": locator_result.get("target_text"),
        "comment_text": comment_text,
        "annotation_action": locator_result.get("annotation_action") or "underline_with_comment",
        "confidence": conf,
        "match_score": match_score,
        "status": "rejected",
        "fallback_type": None,
        "final_underline_paths": [],
        "final_comment_box": None,
        "reason": "",
        "original": locator_result,
    }

    comment_box = _safe_comment_box(
        locator_result.get("comment_box"), target_box, (w, h), comment_text,
        question_bbox=question_bbox,
    )

    # Too little confidence to draw anything precise.
    if conf < SOFT_MARK_CONF_MIN:
        plan["status"] = "feedback_only"
        plan["fallback_type"] = "feedback_only"
        plan["final_comment_box"] = comment_box
        plan["reason"] = f"confidence {conf:.2f} below soft-mark threshold{echo_note}"
        return plan

    # Validate each suggested underline path.
    good_paths: list[list[list[float]]] = []
    smoothing_changed = False
    ink_dropped = 0
    target_w = (target_box[2] - target_box[0]) if target_box else None
    line_est = (float(target_box[3] - target_box[1]) if target_box else max(24.0, h * 0.022))
    for path in locator_result.get("underline_paths") or []:
        pts = _clean_points((path or {}).get("points"), w, h)
        if len(pts) < 2:
            continue
        smoothed_pts = _smooth(pts)
        if any(abs(a[1] - b[1]) > 0.5 or abs(a[0] - b[0]) > 0.5
               for a, b in zip(smoothed_pts, pts)) or len(smoothed_pts) != len(pts):
            smoothing_changed = True
        pts = smoothed_pts
        span = _path_span(pts)
        if target_w:
            if span < target_w * PATH_SPAN_MIN_RATIO or span > target_w * PATH_SPAN_MAX_RATIO:
                continue
            if not _path_near_box(pts, target_box):
                continue
        # Ground truth: an underline must sit under actual ink, not blank paper.
        if ink_mask is not None and _ink_fraction_above_path(ink_mask, pts, line_est) < INK_MIN_FRACTION:
            ink_dropped += 1
            continue
        good_paths.append(pts)
    ink_note = f"; {ink_dropped} path(s) dropped (no ink above baseline)" if ink_dropped else ""

    if good_paths and conf >= CONFIDENCE_MIN:
        plan["final_underline_paths"] = good_paths
        plan["final_comment_box"] = comment_box
        plan["status"] = "smoothed" if smoothing_changed else "ok"
        plan["reason"] = f"valid underline path(s){ink_note}{echo_note}"
        return plan

    # No usable path but we know the text box → safer short soft underline on its baseline
    # — but only when the box actually contains ink (else it's a location miss too).
    if target_box and conf >= SOFT_MARK_CONF_MIN:
        if ink_mask is not None and _ink_fraction_in_box(ink_mask, target_box) < INK_MIN_FRACTION:
            plan["status"] = "feedback_only"
            plan["fallback_type"] = "feedback_only"
            plan["final_comment_box"] = comment_box
            plan["reason"] = f"no ink inside target box (location miss){ink_note}{echo_note}"
            return plan
        x1, y1, x2, y2 = target_box
        baseline = y2 + max(3, int((y2 - y1) * 0.15))
        soft = [[x1, baseline], [(x1 + x2) / 2, baseline], [x2, baseline]]
        plan["final_underline_paths"] = [soft]
        plan["final_comment_box"] = comment_box
        plan["status"] = "soft_mark"
        plan["fallback_type"] = "soft_mark"
        plan["reason"] = f"path unreliable; soft mark along target box baseline{ink_note}{echo_note}"
        return plan

    # Nothing reliable → feedback only.
    plan["status"] = "feedback_only"
    plan["fallback_type"] = "feedback_only"
    plan["final_comment_box"] = comment_box
    plan["reason"] = f"no reliable geometry{ink_note}{echo_note}"
    return plan


def _validate_section(sec: dict, w: int, h: int, ink_mask=None) -> dict | None:
    """Place one positive section's tick BESIDE the located correct line — never on
    top of the student's writing.

    A tick is drawn ONLY when the locator confidently found the student's evidence
    (a valid `evidence_box` and confidence ≥ `TICK_CONF_MIN`, containing actual ink,
    with a passing `read_text` echo when available). Otherwise we return None so the
    caller skips it — we never dump a tick into a blank margin at a guessed spot.
    Placement: the model's own `tick_point` when it sits plausibly left of the
    evidence's first line (it was asked for exactly that); else computed in the left
    margin beside the first line, like a teacher's pen tick beside a good point."""
    conf = _num(sec.get("confidence")) or 0.0
    evid = _box4(sec.get("evidence_box"), w, h)
    if not evid or conf < TICK_CONF_MIN:
        return None
    # Ground truth: the evidence box must contain actual writing.
    if ink_mask is not None and _ink_fraction_in_box(ink_mask, evid) < INK_MIN_FRACTION:
        return None
    # Echo verification: what the locator saw in the box must resemble the evidence
    # it was asked to find (skip-on-miss — a misplaced tick praises the wrong line).
    read_text = (sec.get("read_text") or "").strip()
    evidence_text = (sec.get("evidence_text") or "").strip()
    if read_text and evidence_text and _text_similarity(read_text, evidence_text) < ECHO_MATCH_MIN:
        return None

    x1, y1, x2, y2 = evid
    # Bias the tick toward the FIRST line of the evidence (tall multi-line boxes), so it
    # reads as a tick on the correct point rather than mid-paragraph.
    line_est = min(max(24.0, h * 0.022), float(y2 - y1))

    # Prefer the model's tick_point when it lands where it was asked to: just left of
    # (or at the very start of) the first line of the evidence.
    raw_pt = sec.get("tick_point")
    if isinstance(raw_pt, (list, tuple)) and len(raw_pt) >= 2:
        px, py = _num(raw_pt[0]), _num(raw_pt[1])
        if (px is not None and py is not None
                and (x1 - 3 * line_est) <= px <= (x1 + line_est)
                and y1 <= py <= (y1 + 1.5 * line_est)
                and 0 <= px <= w and 0 <= py <= h):
            return {
                "section": sec.get("section"),
                "tick_point": [_clamp(px, 8, w - 8), _clamp(py, 8, h - 8)],
                "evidence_box": evid,
                "confidence": conf,
                "source": "model",
            }

    # Computed fallback: left margin beside the first line. If the evidence starts at
    # the page edge (no margin), sit at the start of the line instead — slightly over
    # the line's first word still beats a tick mid-paragraph.
    yc = _clamp(y1 + line_est / 2, 8, h - 8)
    if x1 >= 40:
        tx = max(line_est * 0.6, x1 - line_est * 0.9)
    else:
        tx = x1 + line_est * 0.4
    return {
        "section": sec.get("section"),
        "tick_point": [_clamp(tx, 8, w - 8), yc],
        "evidence_box": evid,
        "confidence": conf,
        "source": "margin",
    }


def validate_question_plan(locator_result: dict, page_size, question_bbox=None,
                           page_png: bytes | None = None) -> dict:
    """Validate a per-question locator result (wrong targets + positive sections).
    Underlines use the safety ladder in `validate_and_smooth`; positive sections become
    a tick ONLY where the evidence was confidently located (skip-on-miss — no margin
    dumping). `question_bbox` is [x, y, w, h] or None. `page_png` (optional) enables
    the ink-presence ground-truth checks against the actual page image."""
    w, h = int(page_size[0]), int(page_size[1])
    ink_mask = build_ink_mask(page_png) if page_png else None

    targets = [
        validate_and_smooth(t, (w, h), question_bbox, ink_mask=ink_mask)
        for t in (locator_result.get("targets") or []) if isinstance(t, dict)
    ]
    raw_sections = [s for s in (locator_result.get("section_marks") or []) if isinstance(s, dict)]
    sections = [
        v for v in (_validate_section(s, w, h, ink_mask=ink_mask) for s in raw_sections)
        if v is not None
    ]
    return {
        "page_number": locator_result.get("page_number"),
        "question_number": locator_result.get("question_number"),
        "targets": targets,
        "section_marks": sections,
    }
