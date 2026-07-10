"""Unit tests for the annotation locator's coordinate conversion — the ONLY place the
model's normalized-0-1000 [y,x] convention is interpreted — plus the scale-sanity
detector and crop_region guards."""
import io

from PIL import Image

from app.ai.agents.annotation_locator_agent import (
    _detect_coord_mode,
    _yx_box_to_px,
    _yx_point_to_px,
    crop_region,
)
from app.ai.agents.answer_extraction_agent import _bbox_to_pixels


class TestDetectCoordMode:
    def test_normalized(self):
        assert _detect_coord_mode([[0, 0, 500, 1000]], 1500, 2000) == "normalized"

    def test_pixel_fallback(self):
        assert _detect_coord_mode([[1100, 200, 1800, 900]], 1500, 2000) == "pixel_fallback"

    def test_invalid_out_of_range(self):
        assert _detect_coord_mode([[5000, 200, 9000, 900]], 1500, 2000) == "invalid"

    def test_invalid_negative(self):
        assert _detect_coord_mode([[-50, 0, 500, 500]], 1500, 2000) == "invalid"

    def test_empty(self):
        assert _detect_coord_mode([None, []], 1500, 2000) == "empty"

    def test_nested_paths_counted(self):
        geoms = [[{"points": [[100, 100], [1200, 400]]}]]
        # 1200 > 1001 but within the 1500x2000 crop → pixels.
        assert _detect_coord_mode(geoms, 1500, 2000) == "pixel_fallback"


class TestDenorm:
    def test_box_normalized(self):
        # [ymin, xmin, ymax, xmax] 0-1000 on a 2000x1000 (w x h) crop → [x1,y1,x2,y2] px.
        assert _yx_box_to_px([100, 250, 500, 750], "normalized", 2000, 1000) == [500.0, 100.0, 1500.0, 500.0]

    def test_box_pixel_fallback_passthrough(self):
        assert _yx_box_to_px([100, 250, 500, 750], "pixel_fallback", 2000, 1000) == [250.0, 100.0, 750.0, 500.0]

    def test_point_normalized_yx_ordering(self):
        # [y, x] = [500, 250] on 2000-wide, 1000-tall crop → [x, y] = [500, 500].
        assert _yx_point_to_px([500, 250], "normalized", 2000, 1000) == [500.0, 500.0]

    def test_malformed(self):
        assert _yx_box_to_px([1, 2], "normalized", 100, 100) is None
        assert _yx_point_to_px("junk", "normalized", 100, 100) is None


class TestExtractionBboxToPixels:
    def test_normalized_to_xywh(self):
        # [ymin, xmin, ymax, xmax] 0-1000 on a 1000x2000 page → [x, y, w, h] px.
        assert _bbox_to_pixels([100, 50, 500, 950], 1000, 2000) == [50, 200, 900, 800]

    def test_pixel_fallback_within_page(self):
        assert _bbox_to_pixels([200, 100, 1800, 900], 1000, 2000) == [100, 200, 800, 1600]

    def test_garbage_rejected(self):
        assert _bbox_to_pixels([9000, 0, 9500, 500], 1000, 2000) is None
        assert _bbox_to_pixels(None, 1000, 2000) is None
        assert _bbox_to_pixels([0, 0, 1, 1], 1000, 2000) is None  # degenerate after denorm


def _png(size=(1600, 2200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, format="PNG")
    return buf.getvalue()


class TestCropRegion:
    def test_good_bbox_cropped(self):
        png = _png()
        crop, origin, cw, ch = crop_region(png, [100, 200, 1200, 900], 1600, 2200)
        assert origin != (0, 0)
        assert cw < 1600 and ch < 2200

    def test_missing_bbox_full_page(self):
        crop, origin, cw, ch = crop_region(_png(), None, 1600, 2200)
        assert origin == (0, 0) and (cw, ch) == (1600, 2200)

    def test_tiny_bbox_falls_back_to_full_page(self):
        crop, origin, cw, ch = crop_region(_png(), [100, 200, 120, 80], 1600, 2200)
        assert origin == (0, 0) and (cw, ch) == (1600, 2200)

    def test_narrow_bbox_falls_back(self):
        # Width < 25% of the page → suspicious → full page.
        crop, origin, cw, ch = crop_region(_png(), [100, 200, 300, 1500], 1600, 2200)
        assert origin == (0, 0)
