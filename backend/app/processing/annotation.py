"""Draw teacher-like annotations on rendered answer-sheet pages.

The AI decides WHAT to mark (emitted as structured instructions); this module
draws them in red on the page image. It deliberately knows nothing about marking
rules — it just renders explicit draw commands with pixel coordinates. Keeping
the drawing dumb means the "specific wrong item only / don't overcrowd" policy
lives in the evaluation + reviewer prompts, not here.

Command shapes (all coordinates in page-image pixels):
    {"type": "underline", "bbox": [x, y, w, h]}
    {"type": "circle",    "bbox": [x, y, w, h]}
    {"type": "comment",   "text": "...", "x": int, "y": int}
    {"type": "mark",      "text": "6/8", "x": int, "y": int}
    {"type": "banner",    "text": "Total: 24 / 40"}     # top-of-page summary strip
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

RED = (213, 43, 30)
RED_FILL = (213, 43, 30, 255)

_FONT_CANDIDATES = [
    "DejaVuSans.ttf",
    "arial.ttf",
    "Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
]
_BOLD_CANDIDATES = [
    "DejaVuSans-Bold.ttf",
    "arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:\\Windows\\Fonts\\arialbd.ttf",
]


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for name in (_BOLD_CANDIDATES if bold else _FONT_CANDIDATES):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _bbox_xywh(cmd: dict) -> tuple[int, int, int, int] | None:
    b = cmd.get("bbox")
    if not isinstance(b, (list, tuple)) or len(b) < 4:
        return None
    try:
        return int(b[0]), int(b[1]), int(b[2]), int(b[3])
    except (TypeError, ValueError):
        return None


def draw_annotations(page_png: bytes, commands: list[dict]) -> bytes:
    """Render annotation commands onto one page; return PNG bytes."""
    img = Image.open(io.BytesIO(page_png)).convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    w, h = img.size

    base = max(14, int(h * 0.018))
    font = _load_font(base)
    mark_font = _load_font(int(base * 1.15), bold=True)
    banner_font = _load_font(int(base * 1.25), bold=True)

    for cmd in commands or []:
        ctype = cmd.get("type")

        if ctype == "underline":
            box = _bbox_xywh(cmd)
            if not box:
                continue
            x, y, bw, bh = box
            line_y = min(h - 2, y + bh + 2)
            draw.line([(x, line_y), (x + bw, line_y)], fill=RED, width=max(2, base // 7))

        elif ctype == "circle":
            box = _bbox_xywh(cmd)
            if not box:
                continue
            x, y, bw, bh = box
            pad = max(3, base // 4)
            draw.ellipse(
                [x - pad, y - pad, x + bw + pad, y + bh + pad],
                outline=RED, width=max(2, base // 7),
            )

        elif ctype == "comment":
            text = (cmd.get("text") or "").strip()
            if not text:
                continue
            x = int(cmd.get("x", int(w * 0.72)))
            y = int(cmd.get("y", 10))
            _draw_text_wrapped(draw, text, x, y, font, max_width=w - x - 8)

        elif ctype == "mark":
            text = (cmd.get("text") or "").strip()
            if not text:
                continue
            x = int(cmd.get("x", int(w * 0.84)))
            y = int(cmd.get("y", 10))
            draw.text((x, y), text, fill=RED, font=mark_font)

        elif ctype == "banner":
            text = (cmd.get("text") or "").strip()
            if not text:
                continue
            _draw_banner(draw, text, w, banner_font)

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _draw_banner(draw: ImageDraw.ImageDraw, text: str, page_w: int, font: ImageFont.FreeTypeFont) -> None:
    pad = 8
    tb = draw.textbbox((0, 0), text, font=font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    x1 = page_w - tw - pad * 3
    draw.rectangle([x1, pad, page_w - pad, pad + th + pad], fill=(255, 255, 255, 235), outline=RED, width=2)
    draw.text((x1 + pad, pad + pad // 2), text, fill=RED, font=font)


def _draw_text_wrapped(
    draw: ImageDraw.ImageDraw, text: str, x: int, y: int,
    font: ImageFont.FreeTypeFont, max_width: int,
) -> None:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for word in words:
        trial = f"{cur} {word}".strip()
        tb = draw.textbbox((0, 0), trial, font=font)
        if (tb[2] - tb[0]) > max_width and cur:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)

    line_h = font.size + 4
    for i, line in enumerate(lines[:6]):
        draw.text((x, y + i * line_h), line, fill=RED, font=font)
