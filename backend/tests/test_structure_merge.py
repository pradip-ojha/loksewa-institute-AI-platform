"""Unit tests for the AI-free merge in the two-call structure agent
(`_merge_blocks_to_structure_map`): folding Call-1 segmentation blocks + Call-2 label
decisions into the legacy `structure_map` shape, plus the Call-2-failure degradation to
Call-1's clearly-written labels. No DB/AI. Run from `backend/`: python -m pytest
"""
from app.ai.agents.answer_structure_agent import _merge_blocks_to_structure_map
from app.modules.subjective.service import assemble_questionwise, validate_structure_map
from types import SimpleNamespace


def _q(number: str, marks: int = 10):
    return SimpleNamespace(question_number=number, question_text=f"Question {number}?", marks=marks)


def _block(bid, pages, label, clarity, *, continues=False):
    return {
        "block_id": bid, "pages": pages,
        "starts_on_page": pages[0], "ends_on_page": pages[-1],
        "continues_across_pages": continues,
        "written_label": label, "label_clarity": clarity,
        "content_summary": "…",
    }


class TestMergeClearLabels:
    def test_clear_labels_passthrough(self):
        blocks = [_block(1, [1], "1", "clear"), _block(2, [2], "2", "clear")]
        labels = {
            1: {"block_id": 1, "final_number": "1", "source": "label", "confidence": 1.0, "note": ""},
            2: {"block_id": 2, "final_number": "2", "source": "label", "confidence": 1.0, "note": ""},
        }
        m = _merge_blocks_to_structure_map(blocks, labels, ["1", "2"])
        nums = {q["question_number"] for q in m["questions"]}
        assert nums == {"1", "2"}
        pages = {p["page"]: p["question_numbers"] for p in m["pages"]}
        assert pages == {1: ["1"], 2: ["2"]}


class TestMergeSplitAcrossPages:
    def test_question_spanning_two_blocks_merges(self):
        # Q1 detected as two blocks on pages 1-2 and 3 → one merged question, union pages.
        blocks = [
            _block(1, [1, 2], "1", "clear", continues=True),
            _block(2, [3], "none", "none"),
        ]
        labels = {
            1: {"block_id": 1, "final_number": "1", "source": "label", "confidence": 1.0, "note": ""},
            2: {"block_id": 2, "final_number": "1", "source": "content", "confidence": 0.7,
                "note": "unlabeled continuation, matched Q1 by content"},
        }
        m = _merge_blocks_to_structure_map(blocks, labels, ["1"])
        assert len(m["questions"]) == 1
        q = m["questions"][0]
        assert q["question_number"] == "1"
        assert q["pages"] == [1, 2, 3]
        assert q["starts_on_page"] == 1 and q["ends_on_page"] == 3
        assert q["continues_across_pages"] is True
        assert "content" in q["note"]


class TestMergeInferredUnclear:
    def test_unclear_block_uses_content_number_with_note(self):
        blocks = [_block(1, [1], "7", "unclear")]
        labels = {
            1: {"block_id": 1, "final_number": "2", "source": "content", "confidence": 0.6,
                "note": "label smudged, matched Q2 by content"},
        }
        m = _merge_blocks_to_structure_map(blocks, labels, ["1", "2", "3"])
        assert [q["question_number"] for q in m["questions"]] == ["2"]
        assert m["questions"][0]["note"]

    def test_devanagari_label_normalized(self):
        blocks = [_block(1, [1], "प्रश्न नं. १", "clear")]
        labels = {1: {"block_id": 1, "final_number": "१", "source": "label", "confidence": 1.0, "note": ""}}
        m = _merge_blocks_to_structure_map(blocks, labels, ["1"])
        assert [q["question_number"] for q in m["questions"]] == ["1"]


class TestClearLabelOverrideAudit:
    def test_overridden_clear_label_gets_backstop_note(self):
        # Call 2 changed a CLEAR label 6 → 4 but forgot the note; merge must record it.
        blocks = [_block(1, [1], "६", "clear")]
        labels = {1: {"block_id": 1, "final_number": "4", "source": "content", "confidence": 0.7, "note": ""}}
        m = _merge_blocks_to_structure_map(blocks, labels, ["4", "6"])
        assert [q["question_number"] for q in m["questions"]] == ["4"]
        note = m["questions"][0]["note"]
        assert "overridden" in note and "6" in note
        assert m["uncertainty_notes"]  # bubbled up to the sheet-level notes

    def test_model_supplied_override_note_not_duplicated(self):
        blocks = [_block(1, [1], "6", "clear")]
        labels = {1: {"block_id": 1, "final_number": "4", "source": "content", "confidence": 0.7,
                      "note": "clear label 6 overridden → 4 by whole answer"}}
        m = _merge_blocks_to_structure_map(blocks, labels, ["4", "6"])
        # backstop must NOT append a second "overridden" phrase when one already exists.
        assert m["questions"][0]["note"].lower().count("overridden") == 1

    def test_kept_clear_label_has_no_override_note(self):
        blocks = [_block(1, [1], "2", "clear")]
        labels = {1: {"block_id": 1, "final_number": "2", "source": "label", "confidence": 1.0, "note": ""}}
        m = _merge_blocks_to_structure_map(blocks, labels, ["1", "2"])
        assert "overridden" not in (m["questions"][0]["note"] or "")


class TestMergeDegradation:
    def test_empty_label_map_uses_clear_labels_only(self):
        # Call 2 failed (empty map): clear labels survive, unclear/none blocks dropped.
        blocks = [
            _block(1, [1], "1", "clear"),
            _block(2, [2], "7", "unclear"),
            _block(3, [3], "none", "none"),
        ]
        m = _merge_blocks_to_structure_map(blocks, {}, ["1", "2", "3"])
        assert [q["question_number"] for q in m["questions"]] == ["1"]

    def test_unknown_final_number_dropped(self):
        blocks = [_block(1, [1], "9", "clear")]
        labels = {1: {"block_id": 1, "final_number": "9", "source": "label", "confidence": 1.0, "note": ""}}
        m = _merge_blocks_to_structure_map(blocks, labels, ["1", "2"])
        assert m["questions"] == []


class TestMergeFeedsDownstream:
    def test_output_shape_survives_validate_and_assemble(self):
        blocks = [_block(1, [1], "1", "clear"), _block(2, [2], "2", "clear")]
        labels = {
            1: {"block_id": 1, "final_number": "1", "source": "label", "confidence": 1.0, "note": ""},
            2: {"block_id": 2, "final_number": "2", "source": "label", "confidence": 1.0, "note": ""},
        }
        m = _merge_blocks_to_structure_map(blocks, labels, ["1", "2"])
        # validate_structure_map must accept it and keep both questions.
        v = validate_structure_map(m, ["1", "2"])
        assert {q["question_number"] for q in v["questions"]} == {"1", "2"}
        # assemble_questionwise must accept it as the structure_map tie-breaker.
        page_outputs = [
            {"page": 1, "page_size": [1000, 1400],
             "answers": [{"question_number": "1", "answer_text": "a1", "question_bbox": [10, 10, 900, 400], "continues": False}]},
            {"page": 2, "page_size": [1000, 1400],
             "answers": [{"question_number": None, "answer_text": "a2", "question_bbox": [10, 10, 900, 400], "continues": False}]},
        ]
        out = assemble_questionwise(page_outputs, [_q("1"), _q("2")], structure_map=v)
        by = {q["qid"]: q["answer_text"] for q in out["questions"]}
        # The unlabeled page-2 answer is attributed to Q2 via the structure map.
        assert by["1"] == "a1"
        assert by["2"] == "a2"
