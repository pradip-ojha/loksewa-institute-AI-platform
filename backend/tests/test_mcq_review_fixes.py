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


# ── Stage B chunking + Stage C ref-alignment (optional-chapter extraction) ───────

from app.ai.agents.mcq_extraction_agent import _split_text_chunks, _build_chapter_list
from app.ai.agents.mcq_topic_assignment_agent import align_assignments


class TestSplitTextChunks:
    def test_short_text_is_one_chunk(self):
        assert _split_text_chunks("line1\nline2", 100) == ["line1\nline2"]

    def test_cuts_on_newline_never_mid_line(self):
        chunks = _split_text_chunks("line1\nline2\nline3\nline4", 12)
        assert chunks == ["line1\nline2", "line3\nline4"]
        # No chunk leads with the swallowed newline.
        assert all(not c.startswith("\n") for c in chunks)

    def test_single_overlong_line_hard_cuts(self):
        assert _split_text_chunks("a" * 25, 10) == ["a" * 10, "a" * 10, "a" * 5]

    def test_exact_boundary(self):
        # Two 5-char lines + newline == 11 chars; limit 11 keeps it whole.
        assert _split_text_chunks("aaaaa\nbbbbb", 11) == ["aaaaa\nbbbbb"]

    def test_empty_or_whitespace_yields_no_chunks(self):
        assert _split_text_chunks("   \n  ") == []
        assert _split_text_chunks("") == []

    def test_no_content_lost_across_seams(self):
        text = "\n".join(f"q{i}" for i in range(50))
        joined = "".join(_split_text_chunks(text, 40)).replace("\n", "")
        assert joined == text.replace("\n", "")


class TestBuildChapterList:
    def test_sorted_numbered_and_filters_unspecified(self):
        out = _build_chapter_list({"Beta", "Alpha", "(unspecified)", ""})
        assert out == "  1. Alpha\n  2. Beta"

    def test_empty_when_no_real_chapters(self):
        assert "no chapters" in _build_chapter_list({"(unspecified)"}).lower()


class TestAlignAssignments:
    def _qs(self, n):
        return [{"question_text": f"q{i}"} for i in range(n)]

    def test_maps_by_ref_not_position(self):
        m = align_assignments(self._qs(3), [{"ref": "R2", "topic": "T"}, {"ref": "R1", "topic": "U"}])
        assert m[0]["topic"] == "U" and m[1]["topic"] == "T" and 2 not in m

    def test_duplicate_ref_keeps_first_only(self):
        m = align_assignments(self._qs(2), [{"ref": "R1", "topic": "first"}, {"ref": "R1", "topic": "dup"}])
        assert m[0]["topic"] == "first" and 1 not in m

    def test_unknown_and_missing_ref_ignored(self):
        m = align_assignments(self._qs(2), [{"ref": "R9", "topic": "X"}, {"topic": "no_ref"}, "garbage"])
        assert m == {}

    def test_non_list_is_empty(self):
        assert align_assignments(self._qs(2), None) == {}
        assert align_assignments(self._qs(2), {"assignments": []}) == {}


# ── Example-question selection + formatting (knowledge-free generation) ───────────

from app.ai.agents.mcq_extraction_agent import _select_diverse, _format_example_questions


def _ex(cid: str, complexity: str) -> dict:
    return {
        "question_text": f"q-{cid}",
        "options": [{"id": "A", "text": "a"}, {"id": "B", "text": "b"}],
        "correct_option_ids": ["A"],
        "explanation": None,
        "complexity": complexity,
    }


class TestSelectDiverse:
    def test_spreads_across_complexities(self):
        pool = ([_ex(f"e{i}", "easy") for i in range(5)]
                + [_ex(f"m{i}", "medium") for i in range(5)]
                + [_ex(f"h{i}", "hard") for i in range(5)])
        picked = _select_diverse(pool, 6)
        kinds = [p["complexity"] for p in picked]
        assert len(picked) == 6
        # Round-robin ⇒ 2 of each within the first 6.
        assert kinds.count("easy") == 2 and kinds.count("medium") == 2 and kinds.count("hard") == 2

    def test_respects_limit(self):
        pool = [_ex(f"m{i}", "medium") for i in range(20)]
        assert len(_select_diverse(pool, 12)) == 12

    def test_pool_smaller_than_limit_returns_all(self):
        pool = [_ex("a", "easy"), _ex("b", "hard")]
        assert len(_select_diverse(pool, 12)) == 2

    def test_all_one_complexity(self):
        pool = [_ex(f"e{i}", "easy") for i in range(4)]
        picked = _select_diverse(pool, 3)
        assert len(picked) == 3 and all(p["complexity"] == "easy" for p in picked)

    def test_untagged_complexity_tops_up(self):
        pool = [_ex("e", "easy"), _ex("x", "unknown"), _ex("y", "weird")]
        picked = _select_diverse(pool, 3)
        assert len(picked) == 3  # untagged still included via top-up

    def test_empty_or_zero_limit(self):
        assert _select_diverse([], 5) == []
        assert _select_diverse([_ex("a", "easy")], 0) == []


class TestFormatExampleQuestions:
    def test_empty_returns_cold_start_guidance(self):
        out = _format_example_questions([])
        assert "No approved example questions yet" in out

    def test_renders_options_correct_marker_and_explanation(self):
        ex = {
            "question_text": "What is X?",
            "options": [{"id": "A", "text": "right"}, {"id": "B", "text": "wrong"}],
            "correct_option_ids": ["A"],
            "explanation": "because",
        }
        out = _format_example_questions([ex])
        assert "Q: What is X?" in out
        assert "[✓] A. right" in out
        assert "[ ] B. wrong" in out
        assert "Explanation: because" in out

    def test_missing_explanation_shows_na(self):
        ex = {"question_text": "q", "options": [{"id": "A", "text": "a"}], "correct_option_ids": ["A"], "explanation": None}
        assert "Explanation: N/A" in _format_example_questions([ex])
