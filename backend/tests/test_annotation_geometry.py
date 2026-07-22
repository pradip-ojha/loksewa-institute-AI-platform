"""Unit tests for the annotation geometry validator: the safety ladder, ink-presence
ground-truth checks, read_text echo verification, tick placement, and comment boxes.
All synthetic-image based — no DB, no AI."""
import io

from PIL import Image, ImageDraw

from app.processing import annotation_geometry as g


def _page_with_ink(size=(1000, 1400), band=(100, 200, 600, 240)) -> bytes:
    """White page with one solid black text band."""
    img = Image.new("L", size, 255)
    ImageDraw.Draw(img).rectangle(list(band), fill=0)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


PAGE = (1000, 1400)
INK_BAND = (100, 200, 600, 240)  # x1, y1, x2, y2


def _target(**over) -> dict:
    base = {
        "question_number": "1",
        "target_text": "the wrong phrase here",
        "read_text": "the wrong phrase here",
        "target_text_box": [100, 195, 600, 245],
        "underline_paths": [{"points": [[100, 250], [300, 252], [500, 251], [600, 250]]}],
        "comment_box": [650, 200, 900, 260],
        "comment_text": "fix this",
        "confidence": 0.8,
    }
    base.update(over)
    return base


class TestValidateAndSmooth:
    def test_good_path_over_ink(self):
        mask = g.build_ink_mask(_page_with_ink())
        plan = g.validate_and_smooth(_target(), PAGE, ink_mask=mask)
        assert plan["status"] in ("ok", "smoothed")
        assert plan["final_underline_paths"]
        assert plan["match_score"] == 1.0

    def test_path_over_blank_paper_demoted(self):
        mask = g.build_ink_mask(_page_with_ink())
        t = _target(
            target_text_box=[100, 795, 600, 845],
            underline_paths=[{"points": [[100, 850], [300, 852], [600, 850]]}],
        )
        plan = g.validate_and_smooth(t, PAGE, ink_mask=mask)
        assert plan["status"] == "feedback_only"
        assert "no ink" in plan["reason"]

    def test_echo_mismatch_caps_confidence(self):
        mask = g.build_ink_mask(_page_with_ink())
        t = _target(read_text="completely unrelated different words entirely")
        plan = g.validate_and_smooth(t, PAGE, ink_mask=mask)
        # Exact underline forbidden; box has ink so the ladder lands on soft_mark.
        assert plan["status"] == "soft_mark"
        assert plan["match_score"] is not None and plan["match_score"] < g.ECHO_MATCH_MIN

    def test_low_confidence_feedback_only(self):
        plan = g.validate_and_smooth(_target(confidence=0.1), PAGE)
        assert plan["status"] == "feedback_only"

    def test_no_ink_mask_skips_ink_checks(self):
        plan = g.validate_and_smooth(_target(), PAGE, ink_mask=None)
        assert plan["status"] in ("ok", "smoothed")

    def test_straight_path_status_ok(self):
        # Perfectly straight, already-ordered path → smoothing changes nothing → "ok".
        t = _target(underline_paths=[{"points": [[100, 250], [350, 250], [600, 250]]}])
        plan = g.validate_and_smooth(t, PAGE)
        assert plan["status"] == "ok"

    def test_comment_never_vanishes_with_question_bbox(self):
        t = _target(comment_box=None, target_text_box=None, confidence=0.2)
        plan = g.validate_and_smooth(t, PAGE, question_bbox=[50, 100, 900, 700])
        assert plan["status"] == "feedback_only"
        assert plan["final_comment_box"] is not None


class TestCommentBox:
    TARGET = [100, 195, 600, 245]

    def _band_top_limit(self, h=1400) -> int:
        return self.TARGET[3] + int(h * g.COMMENT_RANGE_RATIO)

    def test_far_model_box_pulled_into_range(self):
        # The model suggests a spot ~1000px below the underline — the comment must be
        # pulled back into the range band around the target, never left far away.
        mask = g.build_ink_mask(_page_with_ink())
        box = g._safe_comment_box([650, 1200, 900, 1260], self.TARGET, PAGE, "fix this",
                                  ink_mask=mask)
        assert box is not None
        assert box[1] <= self._band_top_limit()

    def test_crowded_candidate_skipped_for_clear_one(self):
        # Right margin full of writing → the search moves on to the clear spot just
        # below the target instead of sitting on the crowding text.
        img = Image.new("L", PAGE, 255)
        d = ImageDraw.Draw(img)
        d.rectangle(list(INK_BAND), fill=0)              # the target's own text
        d.rectangle([610, 150, 995, 500], fill=0)        # crowded right margin
        buf = io.BytesIO(); img.save(buf, format="PNG")
        mask = g.build_ink_mask(buf.getvalue())
        box = g._safe_comment_box(None, self.TARGET, PAGE, "fix this", ink_mask=mask)
        assert box is not None
        assert box[1] >= self.TARGET[3]                  # below the target text
        assert g._ink_fraction_in_box(mask, box) < g.COMMENT_INK_MAX

    def test_all_crowded_stays_in_range(self):
        # Everything near the target is inked → still the least-inked IN-RANGE spot,
        # never the old far-away question-region anchor.
        img = Image.new("L", PAGE, 255)
        ImageDraw.Draw(img).rectangle([0, 0, 1000, 1000], fill=0)
        buf = io.BytesIO(); img.save(buf, format="PNG")
        mask = g.build_ink_mask(buf.getvalue())
        box = g._safe_comment_box(None, self.TARGET, PAGE, "fix this",
                                  question_bbox=[50, 100, 900, 1200], ink_mask=mask)
        assert box is not None
        assert box[1] <= self._band_top_limit()

    def test_no_ink_mask_keeps_priority_order(self):
        # Without a page image the first candidate (the model's own in-range box) wins.
        box = g._safe_comment_box([650, 200, 900, 260], self.TARGET, PAGE, "fix this",
                                  ink_mask=None)
        assert box is not None
        assert box[0] == 650 and box[1] == 200


class TestValidateSection:
    def _sec(self, **over) -> dict:
        base = {
            "section": "Definition",
            "evidence_box": [100, 195, 600, 245],
            "tick_point": [350, 210],  # on the first line of the evidence
            "confidence": 0.9,
            "read_text": "good point text",
            "evidence_text": "good point text",
        }
        base.update(over)
        return base

    def test_model_tick_point_on_line_kept(self):
        mask = g.build_ink_mask(_page_with_ink())
        out = g._validate_section(self._sec(), 1000, 1400, ink_mask=mask)
        assert out["source"] == "model"

    def test_off_text_tick_point_moves_to_line_center(self):
        # The old left-margin spot is no longer acceptable — the tick belongs ON the
        # good text, so an off-text point falls back to the first line's center.
        out = g._validate_section(self._sec(tick_point=[80, 210]), 1000, 1400)
        assert out["source"] == "center"
        assert out["tick_point"][0] == (100 + 600) / 2
        assert 195 <= out["tick_point"][1] <= 245

    def test_low_confidence_skipped(self):
        assert g._validate_section(self._sec(confidence=0.3), 1000, 1400) is None

    def test_blank_evidence_skipped(self):
        mask = g.build_ink_mask(_page_with_ink())
        out = g._validate_section(self._sec(evidence_box=[100, 795, 600, 845]), 1000, 1400, ink_mask=mask)
        assert out is None

    def test_echo_mismatch_skipped(self):
        out = g._validate_section(self._sec(read_text="entirely different unrelated words"), 1000, 1400)
        assert out is None

    def test_no_tick_point_centers_on_first_line(self):
        out = g._validate_section(self._sec(evidence_box=[5, 195, 500, 245], tick_point=None), 1000, 1400)
        assert out is not None
        assert out["source"] == "center"
        assert out["tick_point"][0] == (5 + 500) / 2


class TestValidateQuestionPlan:
    def test_full_plan_with_page_png(self):
        png = _page_with_ink()
        loc = {
            "page_number": 1, "question_number": "1",
            "targets": [_target()],
            "section_marks": [{"section": "s", "evidence_box": [100, 195, 600, 245],
                               "tick_point": [80, 210], "confidence": 0.9}],
        }
        plan = g.validate_question_plan(loc, PAGE, [50, 100, 900, 700], page_png=png)
        assert plan["targets"][0]["status"] in ("ok", "smoothed")
        assert len(plan["section_marks"]) == 1

    def test_no_page_png_back_compat(self):
        loc = {"page_number": 1, "question_number": "1", "targets": [_target()], "section_marks": []}
        plan = g.validate_question_plan(loc, PAGE, None)
        assert plan["targets"][0]["status"] in ("ok", "smoothed")
