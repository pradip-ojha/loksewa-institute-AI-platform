"""Unit tests for MCQ review/test-set pure helpers: regeneration replacement
alignment (`_align_replacements` — key-based via echoed "replaces_ref", positional
only on an exact-count ref-less response) and the single-correct eligibility filter
used by test-set generation. No DB/AI. Run from `backend/`: python -m pytest
"""
import uuid

from app.ai.agents.mcq_extraction_agent import _align_replacements
from app.modules.mcq_tests.service import _eligible_single_correct


def _ids(n: int) -> list:
    return [uuid.uuid4() for _ in range(n)]


def _q(ref: str | None = None) -> dict:
    q = {"question_text": "new question"}
    if ref is not None:
        q["replaces_ref"] = ref
    return q


class TestAlignReplacements:
    def test_refs_map_by_key_not_position(self):
        ids = _ids(3)
        # Model returns them out of order — key wins over position.
        matched, skipped = _align_replacements(ids, [_q("R3"), _q("R1"), _q("R2")])
        assert skipped == []
        assert [qid for qid, _ in matched] == [ids[2], ids[0], ids[1]]

    def test_no_refs_exact_count_falls_back_positional(self):
        ids = _ids(2)
        matched, skipped = _align_replacements(ids, [_q(), _q()])
        assert skipped == []
        assert [qid for qid, _ in matched] == ids

    def test_no_refs_count_mismatch_matches_nothing(self):
        ids = _ids(3)
        matched, skipped = _align_replacements(ids, [_q(), _q()])
        assert matched == []
        assert len(skipped) == 1 and "cannot align safely" in skipped[0]

    def test_unknown_and_duplicate_refs_skipped(self):
        ids = _ids(2)
        matched, skipped = _align_replacements(ids, [_q("R1"), _q("R9"), _q("R1")])
        assert [qid for qid, _ in matched] == [ids[0]]
        assert any("unknown" in s for s in skipped)
        assert any("duplicate" in s for s in skipped)

    def test_partial_refs_skip_only_the_missing(self):
        ids = _ids(2)
        matched, skipped = _align_replacements(ids, [_q("R2"), _q()])
        assert [qid for qid, _ in matched] == [ids[1]]
        assert skipped == ["replacement missing replaces_ref"]

    def test_ref_normalization_case_and_int(self):
        ids = _ids(2)
        matched, skipped = _align_replacements(ids, [_q("r1"), {"question_text": "x", "replaces_ref": 2}])
        assert skipped[:0] == []  # ints without the R prefix are unknown, lowercase r is fine
        assert (ids[0], {"question_text": "new question", "replaces_ref": "r1"}) == matched[0]

    def test_non_dict_payload_counts_as_missing_ref(self):
        ids = _ids(1)
        matched, skipped = _align_replacements(ids, ["garbage", _q("R1")])
        assert [qid for qid, _ in matched] == [ids[0]]
        assert "replacement missing replaces_ref" in skipped


class TestEligibleSingleCorrect:
    def test_single_correct_is_eligible(self):
        assert _eligible_single_correct(["A"]) is True

    def test_multi_correct_excluded(self):
        assert _eligible_single_correct(["A", "C"]) is False

    def test_empty_or_malformed_excluded(self):
        assert _eligible_single_correct([]) is False
        assert _eligible_single_correct(None) is False
        assert _eligible_single_correct("A") is False
