"""Render natural, teacher-like red-pen annotations on answer-sheet pages.

The AI decides WHAT is wrong (checker), WHERE it is (vision locator), and WHETHER
the geometry is safe (annotation_geometry validator). This module only decides HOW
to draw the validated plan.

Two hard lessons baked in here:
  • Answer sheets are Nepali/Devanagari, so EVERY text font must render Devanagari
    (Nirmala UI / Noto Sans Devanagari) — otherwise comments come out as ▯▯▯ tofu.
  • Comments must look like a teacher wrote them with a red pen directly on the sheet:
    plain red ink, NO box, NO background fill, NO border. The locator/validator place
    comments in the margin / blank space so bare red text stays readable without a card.

Underlines still follow the handwriting baseline as a Catmull-Rom curve with light
jitter (never a straight bbox line). It stays uncrowded — it draws only what the
validated plan contains.

Command shapes (all coordinates in page-image pixels):
    {"type": "underline_path", "paths": [[[x, y], ...], ...]}
    {"type": "comment",        "text": "...", "box": [x1, y1, x2, y2]}
    {"type": "mark",           "text": "6/10", "x": int, "y": int}
    {"type": "banner",         "text": "Total: 24 / 40"}
"""
from __future__ import annotations

import io
import math
import random

from PIL import Image, ImageDraw

from app.processing import text_render

RED = (213, 43, 30)
RED_SOFT = (206, 64, 52)

# All annotation text (Nepali/English) is shaped + rasterized by `text_render`
# (HarfBuzz + freetype) so Devanagari matras/conjuncts render correctly, then pasted
# as red "ink". PIL fonts are NOT used for text — they can't shape Devanagari.


# ── curve maths ──────────────────────────────────────────────────────────────────

def _catmull_rom(points: list, samples_per_seg: int = 12) -> list[tuple[float, float]]:
    """Smooth a polyline through its control points (natural baseline curve)."""
    pts = [(float(p[0]), float(p[1])) for p in points if len(p) >= 2]
    if len(pts) < 3:
        return pts
    ext = [pts[0]] + pts + [pts[-1]]
    out: list[tuple[float, float]] = []
    for i in range(1, len(ext) - 2):
        p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]
        for s in range(samples_per_seg):
            t = s / samples_per_seg
            t2, t3 = t * t, t * t * t
            x = 0.5 * ((2 * p1[0]) + (-p0[0] + p2[0]) * t
                       + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                       + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * t
                       + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                       + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            out.append((x, y))
    out.append(pts[-1])
    return out


def _draw_underline_path(draw: ImageDraw.ImageDraw, points: list, base: int, rng: random.Random) -> None:
    curve = _catmull_rom(points)
    if len(curve) < 2:
        return
    width = max(2, base // 7)
    jittered = [(x, y + rng.uniform(-1.0, 1.0)) for (x, y) in curve]
    for i in range(len(jittered) - 1):
        w = max(2, width + (1 if (i % 7 == 0) else 0))
        draw.line([jittered[i], jittered[i + 1]], fill=RED, width=w)
    ghost = [(x, y + max(1.0, width * 0.5)) for (x, y) in jittered]
    for i in range(len(ghost) - 1):
        draw.line([ghost[i], ghost[i + 1]], fill=RED_SOFT, width=max(1, width - 1))


# ── text helpers (HarfBuzz-shaped red ink, no boxes/backgrounds) ───────────────────

def _paste_ink(img: Image.Image, ink: Image.Image, x: int, y: int) -> None:
    """Composite a rendered red-ink RGBA image onto the page, clamped inside it."""
    W, H = img.size
    x = max(2, min(int(x), W - ink.width - 2))
    y = max(2, min(int(y), H - ink.height - 2))
    img.alpha_composite(ink, (x, y))


def _draw_note_card(
    img: Image.Image, text: str, box: tuple[int, int, int, int],
    size: int, rng: random.Random, max_lines: int = 4,
) -> None:
    """Write the comment as bare red pen ink anchored at `box` top-left, wrapped to the
    available width and clamped to the page. No box, no background — like a real teacher."""
    W, H = img.size
    x1, y1, x2, y2 = (int(v) for v in box)
    avail_w = max(140, min(x2, W) - x1) if x2 > x1 else int(W * 0.28)
    avail_w = min(avail_w, W - x1 - 6)

    ink = text_render.render_text_rgba(
        text, size=size, color=RED, max_width=avail_w, max_lines=max_lines,
    )
    if ink.width <= 1:
        return
    cx = max(4, min(x1 + int(rng.uniform(-1, 2)), W - ink.width - 6))
    cy = max(4, min(y1 + int(rng.uniform(-1, 1)), H - ink.height - 4))
    _paste_ink(img, ink, cx, cy)


def _draw_hand_circle(draw: ImageDraw.ImageDraw, cx: float, cy: float, rx: float, ry: float,
                      rng: random.Random, width: int) -> None:
    """Draw an irregular, hand-drawn red circle (slightly past one full turn, with radius
    jitter) — like a teacher circling marks, not a perfect ellipse."""
    n = 46
    turns = 1.07
    start = rng.uniform(0, 2 * math.pi)
    total = 2 * math.pi * turns
    pts: list[tuple[float, float]] = []
    for i in range(n + 1):
        a = start + total * (i / n)
        jr = 1.0 + rng.uniform(-0.06, 0.06)
        pts.append((cx + rx * jr * math.cos(a), cy + ry * jr * math.sin(a)))
    for i in range(len(pts) - 1):
        draw.line([pts[i], pts[i + 1]], fill=RED, width=width)


def _draw_mark_chip(img: Image.Image, text: str, x: int, y: int, size: int, rng: random.Random) -> None:
    """Bold red marks (e.g. '6/10') inside a hand-drawn circle, centered on (x, y) —
    like a teacher circling the marks. No background fill."""
    ink = text_render.render_text_rgba(text, size=size, color=RED, bold=True)
    if ink.width <= 1:
        return
    W, H = img.size
    tw, th = ink.width, ink.height
    rx = tw / 2 + size * 0.5
    ry = th / 2 + size * 0.38
    # Centre, clamped so the whole circle stays on the page.
    cx = min(max(float(x), rx + 4), W - rx - 4)
    cy = min(max(float(y), ry + 4), H - ry - 4)
    draw = ImageDraw.Draw(img, "RGBA")
    _draw_hand_circle(draw, cx, cy, rx, ry, rng, max(3, int(size * 0.09)))
    _paste_ink(img, ink, int(cx - tw / 2), int(cy - th / 2))


def _draw_banner(img: Image.Image, text: str, size: int) -> None:
    """Total written in the top-right corner in bare red pen — no banner box, no fill."""
    W, _ = img.size
    ink = text_render.render_text_rgba(text, size=size, color=RED, bold=True)
    pad = 12
    _paste_ink(img, ink, W - ink.width - pad, pad)


def _draw_tick(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, rng: random.Random) -> None:
    """Draw a big, bold hand-style red check mark (✓) as two strokes — font-independent.
    (x, y) is the elbow of the tick. Drawn thick so it reads like a teacher's pen tick."""
    s = max(16, int(size))
    w = max(4, int(size * 0.1))
    jx, jy = rng.uniform(-1.5, 1.5), rng.uniform(-1.5, 1.5)
    # short down-stroke into the elbow, then a long up-stroke to the upper right
    p0 = (x - s * 0.40 + jx, y - s * 0.05 + jy)
    p1 = (x - s * 0.08 + jx, y + s * 0.34 + jy)
    p2 = (x + s * 0.60 + jx, y - s * 0.55 + jy)
    draw.line([p0, p1], fill=RED, width=w)
    draw.line([p1, p2], fill=RED, width=w)


# ── public entrypoint ────────────────────────────────────────────────────────────

def draw_annotations(page_png: bytes, commands: list[dict]) -> bytes:
    """Render the validated annotation plan for one page; return PNG bytes.

    Command types: underline_path, comment, mark (per-question total), banner
    (sheet total), tick (positive check beside a correct line)."""
    img = Image.open(io.BytesIO(page_png)).convert("RGBA")
    w, h = img.size
    base = max(16, int(h * 0.016))
    mark_size = int(base * 1.4)        # circled per-question total (teacher-scale)
    banner_size = int(base * 1.2)
    tick_size = int(base * 3.2)        # correct-point tick over the line (teacher-scale)
    rng = random.Random((w * 73856093) ^ (h * 19349663) ^ len(commands or []))

    # Underlines + ticks first (on the writing), then text on top so it's never covered.
    draw = ImageDraw.Draw(img, "RGBA")
    for cmd in commands or []:
        ctype = cmd.get("type")
        if ctype == "underline_path":
            for path in cmd.get("paths") or []:
                if isinstance(path, (list, tuple)) and len(path) >= 2:
                    _draw_underline_path(draw, path, base, rng)
        elif ctype == "tick":
            # Keep the whole tick on-page (it spans ~0.4*size left and ~0.6*size right).
            tx = min(max(int(cmd.get("x", 0)), int(tick_size * 0.45)), w - int(tick_size * 0.65))
            ty = min(max(int(cmd.get("y", 0)), int(tick_size * 0.6)), h - int(tick_size * 0.6))
            _draw_tick(draw, tx, ty, tick_size, rng)

    for cmd in commands or []:
        ctype = cmd.get("type")
        if ctype == "comment":
            text = (cmd.get("text") or "").strip()
            box = cmd.get("box")
            if text and isinstance(box, (list, tuple)) and len(box) >= 4:
                _draw_note_card(img, text[:240], tuple(int(v) for v in box[:4]), base, rng)
        elif ctype == "mark":
            text = (cmd.get("text") or "").strip()
            if text:
                _draw_mark_chip(img, text, int(cmd.get("x", int(w * 0.86))), int(cmd.get("y", 12)), mark_size, rng)
        elif ctype == "banner":
            text = (cmd.get("text") or "").strip()
            if text:
                _draw_banner(img, text, banner_size)

    out = io.BytesIO()
    img.convert("RGB").save(out, format="PNG")
    return out.getvalue()
