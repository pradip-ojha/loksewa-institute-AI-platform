"""Unit tests for the Gemini JSON recovery ladder (`_loads_lenient` + `_repair_truncated`).
No network/DB. Covers the real production failure: a preview vision model stopping
mid-response (unterminated Devanagari string / unclosed array/object), which the old
lenient parser could not recover. Run from `backend/`: python -m pytest
"""
from app.ai.providers.gemini import _loads_lenient, _repair_truncated


class TestRepairTruncated:
    def test_unterminated_string_is_closed(self):
        # The exact shape from the worker log: cut off inside a Devanagari read_text.
        bad = '{\n  "targets": [],\n  "section_marks": [\n    {\n      "read_text": "भएका का'
        data = _repair_truncated(bad)
        assert isinstance(data, dict)
        assert data["targets"] == []
        assert data["section_marks"][0]["read_text"].startswith("भएका")

    def test_unclosed_array_and_object(self):
        bad = '{"targets": [{"page_number": 1, "target_text": "x"'
        data = _repair_truncated(bad)
        assert isinstance(data, dict)
        assert data["targets"][0]["target_text"] == "x"

    def test_trailing_comma_dropped(self):
        bad = '{"a": 1, "b": 2,'
        data = _repair_truncated(bad)
        assert data == {"a": 1, "b": 2}

    def test_dangling_colon_filled_with_null(self):
        bad = '{"a": 1, "tick_point":'
        data = _repair_truncated(bad)
        assert data == {"a": 1, "tick_point": None}

    def test_balanced_object_returns_none(self):
        # Not a truncation case — repair must decline so the normal path handles it.
        assert _repair_truncated('{"a": 1}') is None

    def test_garbage_returns_none(self):
        assert _repair_truncated("not json at all") is None


class TestLenientLadder:
    def test_truncated_recovered_via_ladder(self):
        bad = '{\n  "targets": [],\n  "section_marks": [\n    {\n      "read_text": "abc'
        data = _loads_lenient(bad)
        assert isinstance(data, dict) and data["targets"] == []

    def test_control_chars_still_recovered(self):
        # Step 1 (strict=False) path — literal newline inside a string value.
        data = _loads_lenient('{"note": "line1\nline2"}')
        assert data["note"] == "line1\nline2"

    def test_trailing_prose_still_recovered(self):
        # Step 2 (first balanced object) path.
        data = _loads_lenient('{"a": 1}\nHere is your JSON.')
        assert data == {"a": 1}
