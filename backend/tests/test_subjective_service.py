"""Unit tests for the pure (no-DB, no-AI) helpers of the subjective service:
question-number matching (script-normalized), question-wise assembly, and marks
clamping. Run from `backend/`: python -m pytest
"""
from types import SimpleNamespace

from app.modules.subjective.service import (
    _ascii_digits,
    _match_question_number,
    assemble_questionwise,
    clamp_marks,
    validate_structure_map,
)


def _q(number: str, marks: int = 10) -> SimpleNamespace:
    return SimpleNamespace(question_number=number, question_text=f"Question {number}?", marks=marks)


def _page(page: int, answers: list[dict], size=(1000, 1400)) -> dict:
    return {"page": page, "page_size": list(size), "answers": answers, "page_confidence": 0.8}


def _ans(qnum, text, *, bbox=None, continues=False) -> dict:
    return {"question_number": qnum, "answer_text": text,
            "question_bbox": bbox or [10, 10, 900, 400], "continues": continues}


class TestAsciiDigits:
    def test_ascii(self):
        assert _ascii_digits("Q12.") == "12"

    def test_devanagari(self):
        assert _ascii_digits("प्रश्न नं. १८") == "18"

    def test_mixed(self):
        assert _ascii_digits("१a2") == "12"

    def test_empty_and_none(self):
        assert _ascii_digits("") == ""
        assert _ascii_digits(None) == ""

    def test_no_digits(self):
        assert _ascii_digits("क)") == ""


class TestMatchQuestionNumber:
    def test_exact(self):
        assert _match_question_number("Q1", ["Q1", "Q2"]) == "Q1"

    def test_loose_ascii(self):
        assert _match_question_number("1.", ["Q1", "Q2"]) == "Q1"
        assert _match_question_number("Q. 2", ["1", "2"]) == "2"

    def test_devanagari_raw_vs_ascii_valid(self):
        # The historical bug: "१" never matched "1" (no script normalization).
        assert _match_question_number("१", ["1", "2"]) == "1"
        assert _match_question_number("प्रश्न नं. १", ["Q1", "Q2"]) == "Q1"

    def test_ascii_raw_vs_devanagari_valid(self):
        assert _match_question_number("Q2", ["१", "२"]) == "२"

    def test_devanagari_both(self):
        assert _match_question_number("१.", ["१", "२"]) == "१"

    def test_sub_label_with_digit(self):
        # "1a" carries the parent digit → maps to the collapsed parent question.
        assert _match_question_number("1a", ["1", "2"]) == "1"

    def test_letter_only_sub_label_unmatched(self):
        # A bare Devanagari part label has no digit — cannot be attributed here.
        assert _match_question_number("क)", ["1", "2"]) is None

    def test_no_match(self):
        assert _match_question_number("7", ["1", "2"]) is None

    def test_empty(self):
        assert _match_question_number("", ["1"]) is None
        assert _match_question_number(None, ["1"]) is None

    def test_multi_digit_not_prefix_confused(self):
        assert _match_question_number("१२", ["1", "12", "2"]) == "12"


class TestValidateStructureMap:
    def test_normalizes_and_drops(self):
        raw = {
            "questions": [
                {"question_number": "१", "pages": [1], "continues_across_pages": False},
                {"question_number": "garbage", "pages": [2]},
            ],
            "pages": [{"page": 1, "question_numbers": ["१", "Q2", "junk"]}],
            "uncertainty_notes": "note",
        }
        out = validate_structure_map(raw, ["1", "2"])
        assert [q["question_number"] for q in out["questions"]] == ["1"]
        assert out["pages"][0]["question_numbers"] == ["1", "2"]
        assert out["uncertainty_notes"] == "note"

    def test_non_dict(self):
        out = validate_structure_map(None, ["1"])
        assert out == {"questions": [], "pages": [], "uncertainty_notes": ""}


class TestAssembleQuestionwise:
    def test_labeled_answers(self):
        questions = [_q("1"), _q("2")]
        pages = [_page(1, [_ans("१", "first answer"), _ans("2.", "second answer")])]
        out = assemble_questionwise(pages, questions)
        by_qid = {q["qid"]: q for q in out["questions"]}
        assert by_qid["1"]["answer_text"] == "first answer"
        assert by_qid["2"]["answer_text"] == "second answer"

    def test_continuation_carry_forward(self):
        questions = [_q("1"), _q("2")]
        pages = [
            _page(1, [_ans("1", "starts here", continues=True)]),
            _page(2, [_ans(None, "continues here")]),
        ]
        out = assemble_questionwise(pages, questions)
        q1 = next(q for q in out["questions"] if q["qid"] == "1")
        assert q1["answer_text"] == "starts here\ncontinues here"
        assert q1["page_numbers"] == [1, 2]

    def test_structure_map_single_question_tiebreak(self):
        # Unlabeled writing, NOT flagged as continuing, but the structure map says
        # page 2 holds only Q2 → goes to Q2, not last_known (Q1).
        questions = [_q("1"), _q("2")]
        pages = [
            _page(1, [_ans("1", "answer one")]),
            _page(2, [_ans(None, "unlabeled two")]),
        ]
        smap = {"pages": [{"page": 2, "question_numbers": ["2"]}], "questions": []}
        out = assemble_questionwise(pages, questions, structure_map=smap)
        by_qid = {q["qid"]: q for q in out["questions"]}
        assert by_qid["2"]["answer_text"] == "unlabeled two"
        assert by_qid["1"]["answer_text"] == "answer one"

    def test_visible_label_beats_structure_map(self):
        questions = [_q("1"), _q("2")]
        pages = [_page(1, [_ans("2", "clearly labeled two")])]
        smap = {"pages": [{"page": 1, "question_numbers": ["1"]}], "questions": []}
        out = assemble_questionwise(pages, questions, structure_map=smap)
        by_qid = {q["qid"]: q for q in out["questions"]}
        assert by_qid["2"]["answer_text"] == "clearly labeled two"

    def test_pending_continuation_respected_when_in_structure(self):
        questions = [_q("1"), _q("2")]
        pages = [
            _page(1, [_ans("1", "one part a", continues=True)]),
            _page(2, [_ans(None, "one part b")]),
        ]
        smap = {"pages": [{"page": 2, "question_numbers": ["1", "2"]}], "questions": []}
        out = assemble_questionwise(pages, questions, structure_map=smap)
        q1 = next(q for q in out["questions"] if q["qid"] == "1")
        assert "one part b" in q1["answer_text"]

    def test_regions_keep_page_text(self):
        questions = [_q("1")]
        pages = [_page(1, [_ans("1", "text", bbox=[5, 6, 700, 300])])]
        out = assemble_questionwise(pages, questions)
        region = out["questions"][0]["page_regions"][0]
        assert region == {"page": 1, "question_bbox": [5, 6, 700, 300], "answer_text": "text"}


class TestClampMarks:
    def test_hard_cap_and_totals(self):
        questions = [_q("1", marks=10), _q("2", marks=5)]
        evaluation = {"question_results": [
            {"question_number": "1", "awarded_marks": 12, "max_marks": 10},
            {"question_number": "२", "awarded_marks": 3.2, "max_marks": 5},
        ]}
        out, awarded, possible = clamp_marks(evaluation, questions)
        results = {r["question_number"]: r for r in out["question_results"]}
        assert results["1"]["awarded_marks"] == 10        # capped
        assert results["2"]["awarded_marks"] == 3.0       # half-snapped + Devanagari matched
        assert awarded == 13.0 and possible == 15.0

    def test_unmatched_question_recorded(self):
        questions = [_q("1", marks=10)]
        evaluation = {"question_results": [
            {"question_number": "99", "awarded_marks": 4, "max_marks": 7},
        ]}
        out, _, _ = clamp_marks(evaluation, questions)
        assert any("matched no configured question" in n for n in out["clamp_notes"])

    def test_section_redistribution_noted(self):
        # Sections sum to 2 but the question awarded is 6 → 4 marks redistributed.
        questions = [_q("1", marks=10)]
        evaluation = {"question_results": [{
            "question_number": "1", "awarded_marks": 6, "max_marks": 10,
            "sections": [
                {"section": "A", "max_marks": 5, "awarded_marks": 1, "status": "partial",
                 "evidence_text": "x", "note": "n"},
                {"section": "B", "max_marks": 5, "awarded_marks": 1, "status": "partial",
                 "evidence_text": "y", "note": "n"},
            ],
        }]}
        out, _, _ = clamp_marks(evaluation, questions)
        item = out["question_results"][0]
        assert sum(s["awarded_marks"] for s in item["sections"]) == 6.0
        assert any("redistributed" in n for n in out["clamp_notes"])

    def test_consistent_sections_no_note(self):
        questions = [_q("1", marks=10)]
        evaluation = {"question_results": [{
            "question_number": "1", "awarded_marks": 6, "max_marks": 10,
            "sections": [
                {"section": "A", "max_marks": 5, "awarded_marks": 3, "status": "partial",
                 "evidence_text": "x", "note": "n"},
                {"section": "B", "max_marks": 5, "awarded_marks": 3, "status": "partial",
                 "evidence_text": "y", "note": "n"},
            ],
        }]}
        out, _, _ = clamp_marks(evaluation, questions)
        assert "clamp_notes" not in out

    def test_idempotent(self):
        questions = [_q("1", marks=10)]
        evaluation = {"question_results": [
            {"question_number": "1", "awarded_marks": 7.5, "max_marks": 10},
        ]}
        out1, a1, _ = clamp_marks(evaluation, questions)
        out2, a2, _ = clamp_marks(out1, questions)
        assert a1 == a2 == 7.5
        assert "clamp_notes" not in out2
