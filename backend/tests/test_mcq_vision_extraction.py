"""Unit tests for the vision-first MCQ extraction helpers.

Pure-function tests — no DB, no network, no AI calls.
Run from backend/: python -m pytest tests/test_mcq_vision_extraction.py -v
"""
import pytest

from app.ai.agents.mcq_extraction_agent import (
    _normalize_grid_option,
    _reconcile_grid_blocks,
    _to_int,
)


# ── _to_int ───────────────────────────────────────────────────────────────────

class TestToInt:
    def test_arabic_int(self):
        assert _to_int(12) == 12

    def test_arabic_string(self):
        assert _to_int("12") == 12

    def test_arabic_float(self):
        assert _to_int(3.0) == 3

    def test_devanagari_digits(self):
        assert _to_int("१२") == 12

    def test_devanagari_single(self):
        assert _to_int("५") == 5

    def test_zero_returns_none(self):
        assert _to_int(0) is None

    def test_negative_returns_none(self):
        assert _to_int(-1) is None

    def test_empty_string(self):
        assert _to_int("") is None

    def test_none_input(self):
        assert _to_int(None) is None

    def test_non_numeric_string(self):
        assert _to_int("abc") is None

    def test_mixed_float_non_whole(self):
        assert _to_int(3.5) is None

    def test_string_with_spaces(self):
        assert _to_int("  7  ") == 7


# ── _normalize_grid_option ────────────────────────────────────────────────────

class TestNormalizeGridOption:
    def test_uppercase_letters(self):
        assert _normalize_grid_option("A") == "A"
        assert _normalize_grid_option("B") == "B"
        assert _normalize_grid_option("C") == "C"
        assert _normalize_grid_option("D") == "D"

    def test_lowercase_letters(self):
        assert _normalize_grid_option("a") == "A"
        assert _normalize_grid_option("d") == "D"

    def test_nepali_letters(self):
        assert _normalize_grid_option("क") == "A"
        assert _normalize_grid_option("ख") == "B"
        assert _normalize_grid_option("ग") == "C"
        assert _normalize_grid_option("घ") == "D"

    def test_arabic_numerals(self):
        assert _normalize_grid_option("1") == "A"
        assert _normalize_grid_option("2") == "B"
        assert _normalize_grid_option("3") == "C"
        assert _normalize_grid_option("4") == "D"

    def test_devanagari_numerals(self):
        assert _normalize_grid_option("१") == "A"
        assert _normalize_grid_option("२") == "B"
        assert _normalize_grid_option("३") == "C"
        assert _normalize_grid_option("४") == "D"

    def test_invalid(self):
        assert _normalize_grid_option("E") is None
        assert _normalize_grid_option("5") is None
        assert _normalize_grid_option("") is None
        assert _normalize_grid_option(None) is None


# ── _reconcile_grid_blocks ────────────────────────────────────────────────────

def _make_q(n, **extra):
    """Helper: a minimal question dict with question_number n."""
    return {"question_number": n, "question_text": f"Question {n}",
            "options": [{"id": x, "label": x, "text": f"Opt {x}"} for x in "ABCD"],
            **extra}


def _make_grid(entries: dict[int, str]) -> list[dict]:
    """Helper: a list of answer-grid dicts {question_number, correct_option}."""
    return [{"question_number": k, "correct_option": v} for k, v in entries.items()]


class TestReconcileGridBlocks:

    def test_single_block_all_matched(self):
        """Q1-5 followed by a grid: every question gets correct_option_ids."""
        page_results = [
            {"questions": [_make_q(1), _make_q(2), _make_q(3)], "answer_grid": []},
            {"questions": [_make_q(4), _make_q(5)], "answer_grid": []},
            {"questions": [], "answer_grid": _make_grid({1: "A", 2: "B", 3: "C", 4: "D", 5: "A"})},
        ]
        questions, blocks = _reconcile_grid_blocks(page_results)
        assert blocks == 1
        assert len(questions) == 5
        assert questions[0]["correct_option_ids"] == ["A"]
        assert questions[1]["correct_option_ids"] == ["B"]
        assert questions[2]["correct_option_ids"] == ["C"]
        assert questions[3]["correct_option_ids"] == ["D"]
        assert questions[4]["correct_option_ids"] == ["A"]

    def test_single_block_partial_match(self):
        """Grid only covers Q1-Q3; Q4-Q5 have no match → empty correct_option_ids."""
        page_results = [
            {"questions": [_make_q(i) for i in range(1, 6)], "answer_grid": []},
            {"questions": [], "answer_grid": _make_grid({1: "A", 2: "B", 3: "C"})},
        ]
        questions, blocks = _reconcile_grid_blocks(page_results)
        assert blocks == 1
        assert questions[3]["correct_option_ids"] == []
        assert questions[4]["correct_option_ids"] == []

    def test_two_blocks_independent_numbering(self):
        """Two sets of Q1-Q3 with their own grids — numbering restarts after each grid."""
        page_results = [
            {"questions": [_make_q(1), _make_q(2), _make_q(3)], "answer_grid": []},
            {"questions": [], "answer_grid": _make_grid({1: "A", 2: "B", 3: "C"})},
            # Second block starts after the grid
            {"questions": [_make_q(1), _make_q(2), _make_q(3)], "answer_grid": []},
            {"questions": [], "answer_grid": _make_grid({1: "D", 2: "C", 3: "B"})},
        ]
        questions, blocks = _reconcile_grid_blocks(page_results)
        assert blocks == 2
        assert len(questions) == 6
        # First block
        assert questions[0]["correct_option_ids"] == ["A"]
        assert questions[1]["correct_option_ids"] == ["B"]
        assert questions[2]["correct_option_ids"] == ["C"]
        # Second block — same numbers, different answers
        assert questions[3]["correct_option_ids"] == ["D"]
        assert questions[4]["correct_option_ids"] == ["C"]
        assert questions[5]["correct_option_ids"] == ["B"]

    def test_three_blocks(self):
        """Three question+grid pairs."""
        page_results = []
        for block_correct in ["A", "B", "C"]:
            page_results.append({"questions": [_make_q(1)], "answer_grid": []})
            page_results.append({"questions": [], "answer_grid": _make_grid({1: block_correct})})
        questions, blocks = _reconcile_grid_blocks(page_results)
        assert blocks == 3
        assert len(questions) == 3
        assert questions[0]["correct_option_ids"] == ["A"]
        assert questions[1]["correct_option_ids"] == ["B"]
        assert questions[2]["correct_option_ids"] == ["C"]

    def test_grid_spans_two_pages(self):
        """Answer grid split across two page_results — entries accumulate before a new
        question triggers the block flush."""
        page_results = [
            {"questions": [_make_q(1), _make_q(2), _make_q(3)], "answer_grid": []},
            # Grid split: first half
            {"questions": [], "answer_grid": _make_grid({1: "A", 2: "B"})},
            # Grid split: second half
            {"questions": [], "answer_grid": _make_grid({3: "C"})},
            # New block starts here
            {"questions": [_make_q(1)], "answer_grid": _make_grid({1: "D"})},
        ]
        questions, blocks = _reconcile_grid_blocks(page_results)
        assert blocks == 2
        assert len(questions) == 4
        # First block
        assert questions[0]["correct_option_ids"] == ["A"]
        assert questions[1]["correct_option_ids"] == ["B"]
        assert questions[2]["correct_option_ids"] == ["C"]
        # Second block
        assert questions[3]["correct_option_ids"] == ["D"]

    def test_page_with_questions_and_grid(self):
        """A page has questions at the top and the grid at the bottom.
        Questions join the current block; grid closes it."""
        page_results = [
            {"questions": [_make_q(1), _make_q(2)], "answer_grid": []},
            # Page with both: questions (top) then grid (bottom)
            {"questions": [_make_q(3)], "answer_grid": _make_grid({1: "A", 2: "B", 3: "C"})},
        ]
        questions, blocks = _reconcile_grid_blocks(page_results)
        assert blocks == 1
        assert len(questions) == 3
        assert questions[0]["correct_option_ids"] == ["A"]
        assert questions[1]["correct_option_ids"] == ["B"]
        assert questions[2]["correct_option_ids"] == ["C"]

    def test_devanagari_numbers_in_grid(self):
        """Grid uses Devanagari question numbers and Nepali option letters."""
        page_results = [
            {"questions": [_make_q(1), _make_q(2)], "answer_grid": []},
            {"questions": [], "answer_grid": [
                {"question_number": "१", "correct_option": "ख"},
                {"question_number": "२", "correct_option": "ग"},
            ]},
        ]
        questions, blocks = _reconcile_grid_blocks(page_results)
        assert blocks == 1
        assert questions[0]["correct_option_ids"] == ["B"]
        assert questions[1]["correct_option_ids"] == ["C"]

    def test_empty_page_results(self):
        """No input → empty output, zero blocks."""
        questions, blocks = _reconcile_grid_blocks([])
        assert questions == []
        assert blocks == 0

    def test_questions_only_no_grid(self):
        """Questions with no answer grid at all — everything unmatched."""
        page_results = [
            {"questions": [_make_q(1), _make_q(2)], "answer_grid": []},
        ]
        questions, blocks = _reconcile_grid_blocks(page_results)
        assert blocks == 0  # no grid was ever seen
        assert len(questions) == 2
        assert questions[0]["correct_option_ids"] == []
        assert questions[1]["correct_option_ids"] == []

    def test_unmatched_count(self):
        """Questions whose question_number has no grid entry keep empty correct_option_ids."""
        page_results = [
            {"questions": [_make_q(1), _make_q(2), _make_q(3)], "answer_grid": []},
            {"questions": [], "answer_grid": _make_grid({1: "A"})},
        ]
        questions, blocks = _reconcile_grid_blocks(page_results)
        unmatched = sum(1 for q in questions if not q.get("correct_option_ids"))
        assert unmatched == 2  # Q2 and Q3 have no grid entry
