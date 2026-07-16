"""Per-page column-layout detection + column-aware page rendering (pure Python, no AI).

Vision OCR reads a full page at a fixed resolution budget (~768px on the short side), so a
dense two-column Nepali page leaves too few pixels per glyph and the model starts guessing.
This module decides, per page, whether to render the page whole or split it into left/right
column crops (≈2× pixels/glyph, correct left→right reading order).

The detector is deterministic (cv2/numpy ink analysis) and returns a three-way verdict:
- "split"      — a clean central whitespace gutter with real content on both sides and no
                 content crossing it: safe to split (this is the old prod-proven rule plus a
                 crossing guard).
- "whole"      — clearly single-column (or blank / one-sided): render whole, no AI needed.
- "ambiguous"  — a near-miss (table rules through the candidate gutter, unbalanced columns,
                 heading-heavy gutter, off-center gutter): the CALLER may consult a cheap AI
                 layout classifier (ai/agents/page_layout_classifier.py); with no AI answer
                 the safe fallback is WHOLE.

Bias: a false split scrambles reading order and poisons downstream chunks; a missed split
only lowers OCR fidelity. So every "not sure" outcome is ambiguous, and ambiguous-without-AI
degrades to whole.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── Thresholds ───────────────────────────────────────────────────────────────────
# Central band searched for a confident gutter (a two-column layout gutters near centre).
CENTER_BAND = (0.40, 0.60)
# Wider band searched for ambiguity signals only (a 1/3–2/3 layout gutters near 0.35;
# beyond 0.30/0.70 a "column" is more plausibly a sidebar/margin).
WIDE_BAND = (0.30, 0.70)
# A column x is a confident gutter only if it is ink-free over at least this fraction of the
# page height (below 1.0 so a full-width running header/footer band does not disqualify it).
CLEAR_ROWS_CONFIDENT = 0.85
# Below this the best candidate is no plausible gutter at all → whole. Between the two the
# page is ambiguous: dense two-column print rarely drops below ~0.70 clear even with several
# full-width headings, while single-column justified prose clears only ~0.30–0.50 at its
# best centre column (only the line-spacing fraction of rows is empty at any given x).
CLEAR_ROWS_AMBIGUOUS = 0.60
# Each side must carry at least this fraction of the page's total ink for a confident split
# (real content on BOTH sides — guards against splitting an off-centre single column)…
SIDE_INK_CONFIDENT = 0.15
# …between this floor and SIDE_INK_CONFIDENT the thin side could be a nearly-empty real
# column (e.g. a two-column last page) → ambiguous. Below the floor it is marginalia/noise.
SIDE_INK_AMBIGUOUS = 0.05
# Crossing check: window half-width around the candidate gutter, as a fraction of page width.
CROSS_WINDOW_FRAC = 0.04
# A row "spans" the gutter when at least this fraction of its window is ink. A table rule /
# box border is continuous ink through the gutter (even a 1-px hairline fills its row
# window); running text almost never fills 60% of a window centred on a whitespace column.
CROSS_ROW_FILL = 0.60
# Ignore the top/bottom fraction of rows in the crossing check (running headers/footers are
# tolerated exactly as the CLEAR_ROWS_CONFIDENT < 1.0 slack always tolerated them).
CROSS_EXCLUDE_EDGE = 0.10

# Short side of the grayscale raster kept for ambiguous pages (fed to the AI classifier).
ANALYSIS_PNG_MAX_SHORT_SIDE = 768


@dataclass
class LayoutVerdict:
    decision: str                      # "split" | "whole" | "ambiguous"
    gutter_frac: float | None          # candidate gutter x as fraction of page width; set
    #                                    for "split" AND for "ambiguous" with a candidate
    confidence: float                  # 0..1
    signals: list[str] = field(default_factory=list)


@dataclass
class PageLayout:
    page_index: int
    verdict: LayoutVerdict
    # Small grayscale PNG kept ONLY for ambiguous pages (short side ≤768px) so the AI
    # classifier never needs a second render. None for confident pages.
    analysis_png: bytes | None = None


def analyze_ink_mask(ink) -> LayoutVerdict:
    """Decide a page's column layout from its binary ink mask (1 = ink, 0 = background).

    Pure and deterministic — the unit-testable core. First matching rule wins; see the
    module docstring for the split/whole/ambiguous semantics and bias.
    """
    import numpy as np

    h, w = ink.shape
    # 1. Degenerate/blank pages never split and never cost an AI call.
    if w < 40 or h < 40:
        return LayoutVerdict("whole", None, 1.0, ["empty_or_tiny"])
    total_ink = int(ink.sum())
    if total_ink <= 0:
        return LayoutVerdict("whole", None, 1.0, ["empty_or_tiny"])

    col_ink = ink.sum(axis=0)                          # ink pixels per column x
    clear_frac = 1.0 - (col_ink / float(h))            # fraction of rows empty at each x

    def _side_ink(gx: int) -> tuple[float, float]:
        left = int(ink[:, :gx].sum()) / float(total_ink)
        right = int(ink[:, gx:].sum()) / float(total_ink)
        return left, right

    def _crossing(gx: int) -> bool:
        """Any content row spanning the candidate gutter (table rule / box border)?"""
        win = max(3, round(CROSS_WINDOW_FRAC * w))
        x0, x1 = max(0, gx - win), min(w, gx + win + 1)
        y0, y1 = int(CROSS_EXCLUDE_EDGE * h), int((1.0 - CROSS_EXCLUDE_EDGE) * h)
        if y1 <= y0 or x1 <= x0:
            return False
        window = ink[y0:y1, x0:x1]
        row_fill = window.mean(axis=1)
        return bool((row_fill >= CROSS_ROW_FILL).any())

    # Best candidate in the central band.
    cx0, cx1 = int(w * CENTER_BAND[0]), int(w * CENTER_BAND[1])
    if cx1 <= cx0:
        return LayoutVerdict("whole", None, 1.0, ["empty_or_tiny"])
    cx = cx0 + int(clear_frac[cx0:cx1].argmax())
    clear_c = float(clear_frac[cx])
    l, r = _side_ink(cx)
    frac = cx / float(w)

    if clear_c >= CLEAR_ROWS_CONFIDENT and min(l, r) >= SIDE_INK_CONFIDENT:
        if _crossing(cx):
            # 3. A clean gutter with content crossing it: a borderless split would cut a
            #    table/box in half and scramble rows — never auto-split.
            return LayoutVerdict(
                "ambiguous", frac, 0.5, [f"content_crosses_gutter:{clear_c:.2f}"]
            )
        # 2. The prod-proven confident split, now also crossing-guarded.
        return LayoutVerdict("split", frac, clear_c, [f"clean_gutter:{clear_c:.2f}"])

    if clear_c >= CLEAR_ROWS_CONFIDENT:
        thin = min(l, r)
        if thin >= SIDE_INK_AMBIGUOUS:
            # 4. Unbalanced two-column candidate (e.g. a nearly-empty right column on the
            #    chapter's last page) — AI arbitrates.
            return LayoutVerdict(
                "ambiguous", frac, 0.5, [f"side_ink_near_miss:{thin:.2f}"]
            )
        # 5. Off-centre single column: virtually all ink on one side — deterministic whole.
        return LayoutVerdict("whole", None, 0.9, [f"one_sided_content:{thin:.2f}"])

    if clear_c >= CLEAR_ROWS_AMBIGUOUS and min(l, r) >= SIDE_INK_AMBIGUOUS:
        # 6. Heading-heavy two-column page (several full-width headings/figures push the
        #    gutter's clear fraction below the confident bar) vs ordinary prose — AI decides.
        return LayoutVerdict(
            "ambiguous", frac, 0.5, [f"near_miss_clear_rows:{clear_c:.2f}"]
        )

    # 7. Outer band: a confident-looking gutter well off-centre (1/3–2/3 layout vs a wide
    #    indent) is never auto-split — AI arbitrates.
    wx0, wx1 = int(w * WIDE_BAND[0]), int(w * WIDE_BAND[1])
    outer = [x for x in range(wx0, wx1) if not (cx0 <= x < cx1)]
    if outer:
        ox = max(outer, key=lambda x: clear_frac[x])
        clear_o = float(clear_frac[ox])
        if clear_o >= CLEAR_ROWS_CONFIDENT:
            ol, orr = _side_ink(ox)
            if min(ol, orr) >= SIDE_INK_CONFIDENT and not _crossing(ox):
                return LayoutVerdict(
                    "ambiguous", ox / float(w), 0.5, [f"off_center_gutter:{ox / w:.2f}"]
                )

    # 8. No plausible gutter anywhere → ordinary single-column page.
    return LayoutVerdict("whole", None, 0.9, [f"no_gutter:{clear_c:.2f}"])


def _page_ink_mask(page, analysis_dpi: int):
    """Render a fitz page to a cheap grayscale raster and threshold it to an ink mask.

    Returns (gray_image, ink_mask) as numpy arrays. Otsu picks the page-specific
    text/background split; works for vector and scanned pages alike.
    """
    import cv2
    import fitz
    import numpy as np

    mat = fitz.Matrix(analysis_dpi / 72, analysis_dpi / 72)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
    gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    _, ink = cv2.threshold(gray, 0, 1, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    return gray, ink


def analyze_page(page, analysis_dpi: int = 120) -> LayoutVerdict:
    """Deterministic layout verdict for one fitz page. Never raises — a failed analysis
    degrades to a whole-page verdict (detection must never break ingestion)."""
    try:
        _, ink = _page_ink_mask(page, analysis_dpi)
        return analyze_ink_mask(ink)
    except Exception as exc:
        logger.warning("page-layout analysis failed (using whole page): %s", exc)
        return LayoutVerdict("whole", None, 0.0, ["analysis_error"])


def _encode_analysis_png(gray) -> bytes | None:
    """Downscale the analysis raster (short side ≤ ANALYSIS_PNG_MAX_SHORT_SIDE) and encode
    to PNG for the AI classifier. Best-effort — None on failure."""
    import cv2

    try:
        h, w = gray.shape
        short = min(h, w)
        if short > ANALYSIS_PNG_MAX_SHORT_SIDE:
            scale = ANALYSIS_PNG_MAX_SHORT_SIDE / float(short)
            gray = cv2.resize(
                gray, (max(1, round(w * scale)), max(1, round(h * scale))),
                interpolation=cv2.INTER_AREA,
            )
        ok, buf = cv2.imencode(".png", gray)
        return buf.tobytes() if ok else None
    except Exception as exc:
        logger.warning("analysis PNG encode failed: %s", exc)
        return None


def analyze_pdf_layouts(
    data: bytes, page_indices: list[int], analysis_dpi: int = 120
) -> list[PageLayout]:
    """Deterministic layout analysis for the given pages of a PDF. SYNC (cv2/fitz work) —
    run via `asyncio.to_thread`. Each page's 120-DPI gray raster is rendered ONCE; for
    ambiguous pages it is downscaled and kept as `analysis_png` for the AI classifier
    (memory stays bounded because confident pages keep no raster)."""
    import fitz

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        out: list[PageLayout] = []
        for i in page_indices:
            try:
                gray, ink = _page_ink_mask(doc[i], analysis_dpi)
                verdict = analyze_ink_mask(ink)
                png = _encode_analysis_png(gray) if verdict.decision == "ambiguous" else None
            except Exception as exc:
                logger.warning(
                    "page-layout analysis failed on page %d (using whole page): %s", i + 1, exc
                )
                verdict, png = LayoutVerdict("whole", None, 0.0, ["analysis_error"]), None
            out.append(PageLayout(page_index=i, verdict=verdict, analysis_png=png))
        return out
    finally:
        doc.close()


def render_page_regions(
    data: bytes, page_index: int, gutter_frac: float | None, dpi: int = 300
) -> list[bytes]:
    """Render one PDF page to high-fidelity PNG region(s) applying an already-made layout
    DECISION (no re-detection). `gutter_frac=None` → [whole_page_png]; a fraction →
    [left_png, right_png] split exactly on the empty gutter (no glyph cut, no overlap, no
    duplication). PNG is lossless so thin Devanagari strokes are not smeared by JPEG."""
    import fitz

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        page = doc[page_index]
        rect = page.rect
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        if gutter_frac is None:
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
            return [pix.tobytes("png")]
        gx = rect.width * gutter_frac
        clips = [
            fitz.Rect(rect.x0, rect.y0, rect.x0 + gx, rect.y1),   # left column
            fitz.Rect(rect.x0 + gx, rect.y0, rect.x1, rect.y1),   # right column
        ]
        out: list[bytes] = []
        for clip in clips:
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB, clip=clip)
            out.append(pix.tobytes("png"))
        return out
    finally:
        doc.close()


def render_regions_for_decisions(
    data: bytes, decisions: list[tuple[int, float | None]], dpi: int = 300
) -> list[tuple[int, int, bytes]]:
    """Render every (page_index, gutter_frac|None) decision into its column region(s).
    Returns work-units (page_index, region_index, png_bytes) in reading order. SYNC —
    run via `asyncio.to_thread`."""
    import fitz

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        units: list[tuple[int, int, bytes]] = []
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        for page_index, gutter_frac in decisions:
            page = doc[page_index]
            rect = page.rect
            if gutter_frac is None:
                pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
                units.append((page_index, 0, pix.tobytes("png")))
                continue
            gx = rect.width * gutter_frac
            clips = [
                fitz.Rect(rect.x0, rect.y0, rect.x0 + gx, rect.y1),
                fitz.Rect(rect.x0 + gx, rect.y0, rect.x1, rect.y1),
            ]
            for r_idx, clip in enumerate(clips):
                pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB, clip=clip)
                units.append((page_index, r_idx, pix.tobytes("png")))
        return units
    finally:
        doc.close()
