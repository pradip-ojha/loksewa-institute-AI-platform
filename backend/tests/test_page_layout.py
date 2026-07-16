"""Tests for the deterministic page-layout detector (app/processing/page_layout.py).

Synthetic ink masks / fitz-built PDFs only — no DB, no network, no AI.
"""
import io

import numpy as np
import pytest

from app.processing import page_layout as pl

W, H = 1000, 1400


def _blank() -> np.ndarray:
    return np.zeros((H, W), dtype=np.uint8)


def _text_block(ink: np.ndarray, x0: int, x1: int, y0: int = 100, y1: int = 1300,
                line_h: int = 12, gap_h: int = 8) -> None:
    """Simulate justified text lines: alternating ink/space row bands across [x0, x1)."""
    period = line_h + gap_h
    for y in range(y0, y1):
        if (y - y0) % period < line_h:
            ink[y, x0:x1] = 1


class TestAnalyzeInkMask:
    def test_clean_two_column_splits(self):
        ink = _blank()
        _text_block(ink, 50, 470)
        _text_block(ink, 530, 950)
        v = pl.analyze_ink_mask(ink)
        assert v.decision == "split"
        assert v.gutter_frac is not None and 0.45 <= v.gutter_frac <= 0.55
        assert v.confidence >= pl.CLEAR_ROWS_CONFIDENT

    def test_full_width_prose_is_whole(self):
        ink = _blank()
        _text_block(ink, 50, 950)
        v = pl.analyze_ink_mask(ink)
        assert v.decision == "whole"
        assert any(s.startswith("no_gutter") for s in v.signals)

    def test_rules_crossing_gutter_are_ambiguous_never_split(self):
        ink = _blank()
        _text_block(ink, 50, 470)
        _text_block(ink, 530, 950)
        for y in (400, 700, 1000):           # table rules through the gap, mid-page
            ink[y:y + 4, 100:900] = 1
        v = pl.analyze_ink_mask(ink)
        assert v.decision == "ambiguous"
        assert any(s.startswith("content_crosses_gutter") for s in v.signals)

    def test_heading_heavy_two_column_is_ambiguous(self):
        ink = _blank()
        _text_block(ink, 50, 470)
        _text_block(ink, 530, 950)
        # Full-width bands push the gutter's clear fraction into [0.60, 0.85).
        ink[300:475, 50:950] = 1
        ink[700:875, 50:950] = 1
        v = pl.analyze_ink_mask(ink)
        assert v.decision == "ambiguous"
        assert any(s.startswith("near_miss_clear_rows") for s in v.signals)

    def test_thin_second_column_is_ambiguous(self):
        ink = _blank()
        _text_block(ink, 50, 470)                       # big left column
        _text_block(ink, 530, 950, y0=100, y1=220)      # ~8% of ink on the right
        v = pl.analyze_ink_mask(ink)
        assert v.decision == "ambiguous"
        assert any(s.startswith("side_ink_near_miss") for s in v.signals)

    def test_marginalia_only_side_is_whole(self):
        ink = _blank()
        _text_block(ink, 50, 470)
        _text_block(ink, 530, 950, y0=100, y1=130)      # ~2% of ink — noise, not a column
        v = pl.analyze_ink_mask(ink)
        assert v.decision == "whole"
        assert any(s.startswith("one_sided_content") for s in v.signals)

    def test_off_center_gutter_is_ambiguous(self):
        ink = _blank()
        _text_block(ink, 50, 300)
        _text_block(ink, 390, 950)
        v = pl.analyze_ink_mask(ink)
        assert v.decision == "ambiguous"
        assert any(s.startswith("off_center_gutter") for s in v.signals)
        assert v.gutter_frac is not None and 0.28 <= v.gutter_frac <= 0.40

    def test_gap_outside_wide_band_is_whole(self):
        ink = _blank()
        _text_block(ink, 50, 220)
        _text_block(ink, 280, 950)                      # gap at x≈0.25 — beyond WIDE_BAND
        v = pl.analyze_ink_mask(ink)
        assert v.decision == "whole"

    def test_blank_page_is_whole_high_confidence(self):
        v = pl.analyze_ink_mask(_blank())
        assert v.decision == "whole"
        assert v.confidence == 1.0
        assert "empty_or_tiny" in v.signals

    def test_tiny_mask_is_whole(self):
        v = pl.analyze_ink_mask(np.ones((10, 10), dtype=np.uint8))
        assert v.decision == "whole"
        assert "empty_or_tiny" in v.signals

    def test_running_header_does_not_block_split(self):
        ink = _blank()
        _text_block(ink, 50, 470)
        _text_block(ink, 530, 950)
        ink[20:60, 50:950] = 1                          # full-width header in top 10%
        v = pl.analyze_ink_mask(ink)
        assert v.decision == "split"


def _two_column_pdf() -> bytes:
    """A one-page PDF whose 'text' is filled bars in two column ranges."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=500, height=700)
    for y in range(50, 650, 14):
        page.draw_rect(fitz.Rect(25, y, 235, y + 8), fill=(0, 0, 0), color=None)
        page.draw_rect(fitz.Rect(265, y, 475, y + 8), fill=(0, 0, 0), color=None)
    data = doc.tobytes()
    doc.close()
    return data


class TestPdfAnalysisAndRendering:
    def test_analyze_pdf_layouts_detects_two_columns(self):
        layouts = pl.analyze_pdf_layouts(_two_column_pdf(), [0])
        assert len(layouts) == 1
        assert layouts[0].verdict.decision == "split"
        assert layouts[0].analysis_png is None          # kept only for ambiguous pages

    def test_render_split_returns_two_regions_covering_page(self):
        from PIL import Image
        data = _two_column_pdf()
        verdict = pl.analyze_pdf_layouts(data, [0])[0].verdict
        pngs = pl.render_page_regions(data, 0, verdict.gutter_frac, dpi=150)
        assert len(pngs) == 2
        whole = pl.render_page_regions(data, 0, None, dpi=150)
        assert len(whole) == 1
        widths = [Image.open(io.BytesIO(p)).size[0] for p in pngs]
        full_w = Image.open(io.BytesIO(whole[0])).size[0]
        assert abs(sum(widths) - full_w) <= 3           # split covers the page, no overlap

    def test_render_regions_for_decisions_reading_order(self):
        data = _two_column_pdf()
        units = pl.render_regions_for_decisions(data, [(0, 0.5)], dpi=100)
        assert [(p, r) for p, r, _ in units] == [(0, 0), (0, 1)]
        units_whole = pl.render_regions_for_decisions(data, [(0, None)], dpi=100)
        assert [(p, r) for p, r, _ in units_whole] == [(0, 0)]


class TestAssemblePageTexts:
    """Mixed clean/garbage page reassembly for MCQ ingestion (knowledge_processing_agent)."""

    def _assemble(self, *args):
        from app.ai.agents.knowledge_processing_agent import _assemble_page_texts
        return _assemble_page_texts(*args)

    def test_mixed_pages_keep_page_order(self):
        out = self._assemble(
            ["clean1", "k[jf/L umbrella", "clean3"],
            ["valid_unicode", "legacy_font", "valid_unicode"],
            {1: "OCR2"},
        )
        assert out == "clean1\n\nOCR2\n\nclean3"

    def test_failed_ocr_page_is_skipped(self):
        out = self._assemble(
            ["clean1", "garbage", "clean3"],
            ["valid_unicode", "legacy_font", "valid_unicode"],
            {},                                             # OCR failed → no entry
        )
        assert out == "clean1\n\nclean3"

    def test_all_empty_returns_empty_string(self):
        out = self._assemble(
            ["", ""],
            ["empty", "empty"],
            {0: "", 1: ""},
        )
        assert out == ""

    def test_ocr_wins_over_text_layer_on_a_valid_unicode_page(self):
        """FORCE_OCR_ALL_PAGES OCRs every page, including `valid_unicode` ones. The OCR
        text must win: a Preeti body under a single Unicode Devanagari heading classifies
        `valid_unicode`, so trusting the text layer there would ingest ASCII garbage."""
        out = self._assemble(
            ["नेपाल k[jf/L garbage-body", "clean2"],
            ["valid_unicode", "valid_unicode"],
            {0: "OCR1", 1: "OCR2"},
        )
        assert out == "OCR1\n\nOCR2"

    def test_ocr_failure_on_an_ocrd_page_skips_it(self):
        """An OCR'd page whose OCR came back empty is skipped — never backfilled from the
        raw text layer (garbage for this corpus)."""
        out = self._assemble(
            ["नेपाल k[jf/L garbage-body", "clean2"],
            ["valid_unicode", "valid_unicode"],
            {0: "", 1: "OCR2"},
        )
        assert out == "OCR2"
