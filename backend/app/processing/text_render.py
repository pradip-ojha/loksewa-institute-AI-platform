"""Shaping-aware mixed Devanagari/Latin text rendering for checked-PDF annotations.

PIL's ``ImageDraw.text`` does NOT shape complex scripts unless Pillow was built with
raqm — which the Windows/standard wheels are not — so Devanagari matras and conjuncts
(संयुक्त अक्षर) come out broken. We avoid that entirely: shape the text ourselves with
**uharfbuzz** (HarfBuzz) and rasterize each glyph with **freetype-py**, composing onto
a transparent RGBA image. That image is then pasted on the page like red-pen ink.

Comments are mixed-script ("मुख्य बुँदा छुट्यो", "Add example", "Total: 24 / 40"), and no
single bundled font covers both well, so we itemize the text into runs and pick a font
per character: **Noto Sans Devanagari** for Devanagari (+ shared digits/punct), with
**Noto Sans** (Latin) as the fallback for everything it lacks. Both fonts are bundled in
``fonts/`` so rendering is deterministic and needs no system libraqm.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache

import freetype
import uharfbuzz as hb
from PIL import Image

logger = logging.getLogger(__name__)

_FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")

# (regular, bold) bundled-first, then system fallbacks, per font role.
_FONTS = {
    "deva": {
        False: [os.path.join(_FONT_DIR, "NotoSansDevanagari-Regular.ttf"),
                "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
                "C:/Windows/Fonts/Nirmala.ttc"],
        True: [os.path.join(_FONT_DIR, "NotoSansDevanagari-Bold.ttf"),
               "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
               "C:/Windows/Fonts/Nirmala.ttc"],
    },
    "latin": {
        False: [os.path.join(_FONT_DIR, "NotoSans-Regular.ttf"),
                "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "C:/Windows/Fonts/arial.ttf"],
        True: [os.path.join(_FONT_DIR, "NotoSans-Bold.ttf"),
               "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
               "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
               "C:/Windows/Fonts/arialbd.ttf"],
    },
}


def _resolve(kind: str, bold: bool) -> str:
    candidates = _FONTS[kind][bold]
    bundled = candidates[0]
    for i, path in enumerate(candidates):
        if os.path.exists(path):
            if i != 0:
                logger.warning(
                    "Bundled %s font missing at %s — falling back to system font %s. "
                    "Commit the font to app/processing/fonts/ for consistent rendering.",
                    kind, bundled, path,
                )
            return path
    # Fall back to the regular weight if a bold file is missing.
    if bold:
        return _resolve(kind, False)
    raise FileNotFoundError(
        f"No {kind} font found; expected the bundled font at {bundled}. "
        "Ensure app/processing/fonts/ is included in the deploy."
    )


@lru_cache(maxsize=16)
def _hb_face(kind: str, bold: bool) -> hb.Face:
    with open(_resolve(kind, bold), "rb") as fh:
        return hb.Face(fh.read())


@lru_cache(maxsize=16)
def _ft_face(kind: str, bold: bool) -> "freetype.Face":
    return freetype.Face(_resolve(kind, bold))


def _covered(kind: str, bold: bool, ch: str) -> bool:
    return _ft_face(kind, bold).get_char_index(ord(ch)) != 0


def _itemize(text: str, bold: bool) -> list[tuple[str, str]]:
    """Split `text` into (font_kind, run_text) preferring Devanagari, Latin as fallback.
    Spaces stay with the current run so word spacing is preserved."""
    runs: list[tuple[str, str]] = []
    cur_kind: str | None = None
    cur = ""
    for ch in text:
        if ch == " ":
            kind = cur_kind or "latin"
        elif _covered("deva", bold, ch):
            kind = "deva"
        elif _covered("latin", bold, ch):
            kind = "latin"
        else:
            kind = "deva"  # render .notdef rather than drop the char
        if kind != cur_kind and cur:
            runs.append((cur_kind, cur))
            cur = ""
        cur_kind = kind
        cur += ch
    if cur:
        runs.append((cur_kind, cur))
    return runs


def _shape_run(kind: str, text: str, size: int, bold: bool):
    face = _hb_face(kind, bold)
    font = hb.Font(face)
    upem = face.upem
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf)
    scale = size / float(upem)
    return buf.glyph_infos, buf.glyph_positions, scale


def measure_text(text: str, size: int, *, bold: bool = False) -> tuple[int, int]:
    """Return (width, height) in pixels for a single line of mixed-script shaped text."""
    if not text:
        return (0, size)
    width = 0.0
    for kind, run in _itemize(text, bold):
        _, positions, scale = _shape_run(kind, run, size, bold)
        width += sum(p.x_advance for p in positions) * scale
    return (max(1, int(round(width))), max(1, int(round(size * 1.35))))


def render_line_rgba(text: str, *, size: int, color: tuple[int, int, int],
                     bold: bool = False) -> Image.Image:
    """Render ONE line of mixed-script shaped text to a tight RGBA image.

    Two passes: rasterize every shaped glyph (across font runs) recording placement +
    extents against a baseline at y=0, then size the canvas from the actual extents so
    above-headline matra parts (े ी ो ँ ै) and descenders are never clipped."""
    if not text:
        return Image.new("RGBA", (1, max(1, size)), (0, 0, 0, 0))

    r, g, b = color
    placed: list[tuple[Image.Image, int, int]] = []  # (glyph_rgba, x, y_from_baseline)
    pen_x = 0.0
    min_y, max_y, max_x = 0, size, 0

    for kind, run in _itemize(text, bold):
        infos, positions, scale = _shape_run(kind, run, size, bold)
        ft = _ft_face(kind, bold)
        ft.set_pixel_sizes(0, size)
        for info, pos in zip(infos, positions):
            ft.load_glyph(info.codepoint, freetype.FT_LOAD_RENDER)
            bmp = ft.glyph.bitmap
            gw, gh = bmp.width, bmp.rows
            x = int(round(pen_x + pos.x_offset * scale + ft.glyph.bitmap_left))
            y = int(round(-ft.glyph.bitmap_top - pos.y_offset * scale))  # baseline=0, up negative
            if gw and gh:
                alpha = Image.frombytes("L", (gw, gh), bytes(bmp.buffer))
                glyph_rgba = Image.new("RGBA", (gw, gh), (r, g, b, 0))
                glyph_rgba.putalpha(alpha)
                placed.append((glyph_rgba, x, y))
                min_y = min(min_y, y)
                max_y = max(max_y, y + gh)
                max_x = max(max_x, x + gw)
            pen_x += pos.x_advance * scale

    max_x = max(max_x, int(round(pen_x)))
    width = max_x + 4
    baseline_y = -min_y + 1
    height = (max_y - min_y) + 2
    canvas = Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))
    for glyph_rgba, x, y in placed:
        canvas.alpha_composite(glyph_rgba, (x, baseline_y + y))
    return canvas


def wrap_text(text: str, max_width: int, size: int, *, bold: bool = False) -> list[str]:
    """Greedy word-wrap using shaped measurement so wrapped lines fit `max_width`."""
    words = (text or "").split()
    if not words:
        return []
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if measure_text(trial, size, bold=bold)[0] > max_width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def render_text_rgba(text: str, *, size: int, color: tuple[int, int, int],
                     bold: bool = False, max_width: int | None = None,
                     max_lines: int | None = None, line_spacing: float = 1.15) -> Image.Image:
    """Render (optionally wrapped, multi-line) mixed-script shaped text to a tight RGBA image."""
    lines = wrap_text(text, max_width, size, bold=bold) if max_width else [text]
    if max_lines:
        lines = lines[:max_lines]
    if not lines:
        return Image.new("RGBA", (1, max(1, size)), (0, 0, 0, 0))

    rendered = [render_line_rgba(ln, size=size, color=color, bold=bold) for ln in lines]
    step = int(round(size * 1.35 * line_spacing))
    width = max(im.width for im in rendered)
    height = step * (len(rendered) - 1) + max(im.height for im in rendered)
    canvas = Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))
    for i, im in enumerate(rendered):
        canvas.alpha_composite(im, (0, i * step))
    return canvas
