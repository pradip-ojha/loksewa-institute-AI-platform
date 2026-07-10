"""Unit tests for the shared fuzzy text-matching helpers (Devanagari-aware)."""
from app.processing.text_match import ngrams, norm_text, similarity


class TestNormText:
    def test_keeps_devanagari_letters(self):
        # Base Devanagari letters are kept; combining matras/halant (Mn/Mc marks, not
        # alphanumeric) are stripped — a crude consonant-skeleton that is deliberately
        # tolerant of the matra-level OCR variance common in handwriting reads.
        out = norm_text("नेपाल राष्ट्र बैंक!")
        assert "न" in out and "प" in out and "ब" in out
        assert " " not in out and "!" not in out

    def test_same_word_different_matras_still_match(self):
        assert norm_text("गर्नु") == norm_text("गरनु")  # halant/matra variance collapses

    def test_lowercase_alnum(self):
        assert norm_text("Hello, World 42!") == "helloworld42"

    def test_none(self):
        assert norm_text(None) == ""


class TestSimilarity:
    def test_identical(self):
        assert similarity("the wrong phrase", "the wrong phrase") == 1.0

    def test_substring(self):
        assert similarity("wrong phrase", "student wrote the wrong phrase here") == 1.0

    def test_unrelated(self):
        assert similarity("completely different words", "नेपाल राष्ट्र बैंकको काम") < 0.2

    def test_partial_overlap_between(self):
        s = similarity("बैंकको मुख्य काम निक्षेप संकलन", "बैंकको काम भनेको निक्षेप संकलन गर्नु हो")
        assert 0.3 < s < 1.0

    def test_empty(self):
        assert similarity("", "anything") == 0.0
        assert similarity(None, None) == 0.0

    def test_ngrams_short_string(self):
        assert ngrams("ab") == {"ab"}
        assert ngrams("") == set()
