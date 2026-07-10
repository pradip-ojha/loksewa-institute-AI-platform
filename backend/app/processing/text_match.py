"""Tiny shared fuzzy text-matching helpers (Devanagari-aware).

Used to route positive-section ticks to the right page (workers subjective tasks) and
to verify the annotation locator's `read_text` echo against the checker's target text
(annotation geometry validator). Pure Python, no AI.
"""
from __future__ import annotations


def norm_text(s: str | None) -> str:
    """Lowercased alphanumeric-only form (keeps Devanagari letters) for fuzzy matching."""
    return "".join(ch.lower() for ch in (s or "") if ch.isalnum())


def ngrams(s: str, n: int = 4) -> set[str]:
    return {s[i:i + n] for i in range(len(s) - n + 1)} if len(s) >= n else ({s} if s else set())


def similarity(a: str | None, b: str | None) -> float:
    """Fuzzy similarity of `a` against `b` in [0, 1]: substring shortcut, else the
    fraction of `a`'s character n-grams found in `b`. Asymmetric on purpose — `a`
    should be the (shorter) probe text, `b` the reference."""
    na, nb = norm_text(a), norm_text(b)
    if not na or not nb:
        return 0.0
    if na in nb or nb in na:
        return 1.0
    ga = ngrams(na)
    if not ga:
        return 0.0
    return len(ga & ngrams(nb)) / len(ga)
