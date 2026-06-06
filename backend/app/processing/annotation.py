"""Render natural, teacher-like red-pen annotations on answer-sheet pages.

The AI decides WHAT is wrong (checker), WHERE it is (vision locator), and WHETHER
the geometry is safe (annotation_geometry validator). This module only decides HOW
to draw the validated plan.

Two hard lessons baked in here:
  • Answer sheets are Nepali/Devanagari, so EVERY text font must render Devanagari
    (Nirmala UI / Noto Sans Devanagari) — otherwise comments come out as ▯▯▯ tofu.
  • Real answer sheets fill the page edge-to-edge, so a bare red comment lands on top
    of the student's writing and becomes unreadable. Comments and marks are therefore
    drawn on a translucent white "margin-note" card with a thin red border, like a
    teacher's sticky note — always legible regardless of what is underneath.

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
import random

from PIL import Image, ImageDraw, ImageFont

RED = (213, 43, 30)
RED_SOFT = (206, 64, 52)
CARD_FILL = (255, 255, 252, 232)   # near-white, slightly translucent
CARD_BORDER = (213, 43, 30)

# Devanagari-capable first (Windows: Nirmala UI; Linux/prod: Noto Sans Devanagari),
# then plain sans as a last resort. A handwriting font is intentionally NOT used for
# body text because none of the common ones cover Devanagari.
_FONT_CANDIDATES = [
    "C:/Windows/Fonts/Nirmala.ttc",
    "Nirmala.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    "NotoSansDevanagari-Regular.ttf",
    "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
    "C:/Windows/Fonts/mangal.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "DejaVuSans.ttf",
    "arial.ttf",
]
_BOLD_CANDIDATES = [
    "C:/Windows/Fonts/Nirmala.ttc",
    "Nirmala.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
    "NotoSansDevanagari-Bold.ttf",
    "C:/Windows/Fonts/mangal.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",
    "arialbd.ttf",
]


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for name in (_BOLD_CANDIDATES if bold else _FONT_CANDIDATES):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


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


# ── text helpers ─────────────────────────────────────────────────────────────────

def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    cur = ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textbbox((0, 0), trial, font=font)[2] > max_width and cur:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def _round_rect(draw: ImageDraw.ImageDraw, box, radius: int, fill, outline, width: int) -> None:
    try:
        draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)
    except Exception:
        draw.rectangle(box, fill=fill, outline=outline, width=width)


def _draw_note_card(
    img: Image.Image, text: str, box: tuple[int, int, int, int],
    font: ImageFont.FreeTypeFont, max_lines: int = 4,
) -> None:
    """Draw red text on a translucent white card anchored at `box` top-left, sized to
    the wrapped text and clamped to the page. Always legible over writing."""
    W, H = img.size
    x1, y1, x2, y2 = (int(v) for v in box)
    pad = max(6, font.size // 3)
    avail_w = max(120, min(x2, W) - x1 - 2 * pad) if x2 > x1 else int(W * 0.26)
    avail_w = min(avail_w, W - x1 - 2 * pad - 4)

    probe = ImageDraw.Draw(img)
    lines = _wrap(probe, text, font, avail_w)[:max_lines]
    if not lines:
        return
    line_h = font.size + 6
    text_w = max((probe.textbbox((0, 0), ln, font=font)[2] for ln in lines), default=avail_w)
    card_w = min(W - 8, text_w + 2 * pad)
    card_h = line_h * len(lines) + 2 * pad

    # Keep the whole card on the page.
    cx1 = max(4, min(x1, W - card_w - 4))
    cy1 = max(4, min(y1, H - card_h - 4))
    card = (cx1, cy1, cx1 + card_w, cy1 + card_h)

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    _round_rect(od, card, radius=max(6, pad), fill=CARD_FILL, outline=CARD_BORDER, width=2)
    for i, ln in enumerate(lines):
        od.text((cx1 + pad, cy1 + pad + i * line_h), ln, fill=RED, font=font)
    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"), (0, 0))


def _draw_mark_chip(img: Image.Image, text: str, x: int, y: int, font: ImageFont.FreeTypeFont) -> None:
    """Small white chip with bold red marks (e.g. '6/10'), clamped to the page."""
    W, H = img.size
    probe = ImageDraw.Draw(img)
    tb = probe.textbbox((0, 0), text, font=font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    pad = max(5, font.size // 4)
    cw, ch = tw + 2 * pad, th + 2 * pad
    cx1 = max(2, min(int(x), W - cw - 2))
    cy1 = max(2, min(int(y), H - ch - 2))
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    _round_rect(od, (cx1, cy1, cx1 + cw, cy1 + ch), radius=max(5, pad), fill=CARD_FILL, outline=CARD_BORDER, width=2)
    od.text((cx1 + pad - tb[0], cy1 + pad - tb[1]), text, fill=RED, font=font)
    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"), (0, 0))


def _draw_banner(img: Image.Image, text: str, font: ImageFont.FreeTypeFont) -> None:
    W, _ = img.size
    probe = ImageDraw.Draw(img)
    pad = 12
    tb = probe.textbbox((0, 0), text, font=font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    cw, ch = tw + 2 * pad, th + 2 * pad
    cx1 = W - cw - pad
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    _round_rect(od, (cx1, pad, cx1 + cw, pad + ch), radius=8, fill=(255, 255, 255, 240), outline=CARD_BORDER, width=3)
    od.text((cx1 + pad - tb[0], pad + pad - tb[1]), text, fill=RED, font=font)
    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"), (0, 0))


# ── public entrypoint ────────────────────────────────────────────────────────────

def draw_annotations(page_png: bytes, commands: list[dict]) -> bytes:
    """Render the validated annotation plan for one page; return PNG bytes."""
    img = Image.open(io.BytesIO(page_png)).convert("RGB")
    w, h = img.size
    base = max(16, int(h * 0.016))
    rng = random.Random((w * 73856093) ^ (h * 19349663) ^ len(commands or []))

    font = _load_font(base)
    mark_font = _load_font(int(base * 1.2), bold=True)
    banner_font = _load_font(int(base * 1.3), bold=True)

    # Underlines first (on the writing), then cards on top so they're never covered.
    draw = ImageDraw.Draw(img, "RGBA")
    for cmd in commands or []:
        if cmd.get("type") == "underline_path":
            for path in cmd.get("paths") or []:
                if isinstance(path, (list, tuple)) and len(path) >= 2:
                    _draw_underline_path(draw, path, base, rng)

    for cmd in commands or []:
        ctype = cmd.get("type")
        if ctype == "comment":
            text = (cmd.get("text") or "").strip()
            box = cmd.get("box")
            if text and isinstance(box, (list, tuple)) and len(box) >= 4:
                _draw_note_card(img, text[:240], tuple(int(v) for v in box[:4]), font)
        elif ctype == "mark":
            text = (cmd.get("text") or "").strip()
            if text:
                _draw_mark_chip(img, text, int(cmd.get("x", int(w * 0.86))), int(cmd.get("y", 12)), mark_font)
        elif ctype == "banner":
            text = (cmd.get("text") or "").strip()
            if text:
                _draw_banner(img, text, banner_font)

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()
