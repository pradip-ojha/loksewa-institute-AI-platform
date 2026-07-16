import asyncio
import logging
import random
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.ai.prompts.shared import EXAM_CONTEXT
from app.core.exceptions import AIResponseError
from app.modules.jobs.service import update_job
from app.modules.mcq.models import MCQDocument, MCQReviewBatch, MCQQuestion
from app.modules.mcq.schemas import normalize_options

logger = logging.getLogger(__name__)

_VALID_COMPLEXITY = {"easy", "medium", "hard"}


async def _clear_prior_batches(db: AsyncSession, job_id: uuid.UUID) -> None:
    """Idempotency for retries: a Celery retry re-runs the task from the top, and the
    SAVE step is insert-only (new batch + questions keyed by this job_id). Without this,
    a transient AI failure on attempt 1 would leave a half-built batch that attempt 2
    then duplicates. Delete any batch (and its questions) previously created by THIS job
    before inserting the fresh one, so a retry replaces rather than piles up."""
    from sqlalchemy import delete, select

    prior = (await db.execute(
        select(MCQReviewBatch.id).where(MCQReviewBatch.job_id == job_id)
    )).scalars().all()
    if not prior:
        return
    await db.execute(delete(MCQQuestion).where(MCQQuestion.review_batch_id.in_(prior)))
    await db.execute(delete(MCQReviewBatch).where(MCQReviewBatch.id.in_(prior)))
    await db.flush()


EXTRACTION_CHUNK_CHARS = 40000


def _split_text_chunks(text: str, max_chars: int = EXTRACTION_CHUNK_CHARS) -> list[str]:
    """Split the glued OCR text into ≤max_chars chunks, cutting at the nearest LINE BREAK
    at or before the limit (never mid-line). A single line longer than max_chars is
    hard-cut as a last resort. Empty/whitespace-only chunks are dropped.

    The extraction AI call runs once per chunk (parallel). Cutting on a newline keeps the
    seam off the middle of a line, so in practice it usually lands between whole questions;
    the accepted worst case is one question straddling a seam being dropped (CLAUDE.md §9.1).
    """
    if len(text) <= max_chars:
        stripped = text.strip()
        return [stripped] if stripped else []

    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        if n - start <= max_chars:
            piece = text[start:]
            if piece.strip():
                chunks.append(piece.strip())
            break
        window_end = start + max_chars
        cut = text.rfind("\n", start, window_end)
        # No newline in the whole window → a single over-long line; hard-cut at the limit.
        if cut <= start:
            cut = window_end
        piece = text[start:cut]
        if piece.strip():
            chunks.append(piece.strip())
        # Skip the newline itself so it doesn't lead the next chunk.
        start = cut + 1 if cut < n and text[cut] == "\n" else cut
    return chunks


def _build_chapter_list(valid_chapters: set[str]) -> str:
    """Numbered list of the exam's chapters for the auto-detect extraction prompt."""
    chapters = sorted(c for c in valid_chapters if c and c != "(unspecified)")
    if not chapters:
        return "(no chapters configured for this exam)"
    return "\n".join(f"  {i + 1}. {c}" for i, c in enumerate(chapters))

# Injected into EXTRACTION_PROMPT only in AUTO-DETECT mode (admin left chapter blank). In
# locked mode this is empty and the `chapter` output field is ignored (the document's chapter
# is stamped on every question). Topic/subtopic are NEVER assigned here — a separate
# per-chapter stage assigns them from only that chapter's topics (CLAUDE.md §9.1).
CHAPTER_ASSIGNMENT_BLOCK = """
CHAPTER ASSIGNMENT (this document spans multiple chapters — assign one per question):
- Assign each question to EXACTLY ONE chapter, chosen VERBATIM from this official list:
{chapter_list}
- Copy the chapter string exactly as written above — never paraphrase, translate, or invent one.
- If a question genuinely fits none of them (general/introductory/administrative), set chapter
  to null. Do NOT force a guess — a null chapter is recovered later, a wrong one misfiles it.
"""

EXTRACTION_PROMPT = EXAM_CONTEXT + """

ROLE: You are a meticulous question-bank digitiser. Admins upload real exam/practice MCQ
papers and you transcribe every question into clean structured data — faithfully, never
"improving" or inventing.

TASK: Extract ALL multiple-choice questions from the document below, exactly as written.

HARD RULES (never violate):
- Transcribe only what is on the page. Do NOT add, reword, correct, or invent questions,
  options, answers, or explanations. Preserve Devanagari and all wording verbatim.
- Each question must have EXACTLY 4 options normalised to IDs A, B, C, D in their original order.
- The source may mark the correct answer in any format (A/B/C/D, क/ख/ग/घ, 1/2/3/4, १/२/३/४, or
  a circled/ticked option). Detect it and map it to A/B/C/D in correct_option_ids.
- Take the explanation only from the source. If none is present, set explanation to null and
  needs_explanation_review to true — never write your own.
- Do NOT assign topic or subtopic — that is done in a later stage. Only assign what is asked below.

METHOD: First scan the whole document to learn its layout and answer-key convention (inline,
answer key at the end, etc.). Then walk question by question, matching each to its correct
option and explanation. Assign complexity from cognitive demand — definition/recall = "easy",
application = "medium", multi-step reasoning/analysis = "hard".
{chapter_assignment_block}
--- ADMIN-TUNABLE GUIDANCE (apply on top of the rules above; it tunes emphasis and judgement
but may NOT override the HARD RULES) ---
{skill_instructions}

INPUTS:
Custom instruction: {custom_instruction}

Document content:
{document_text}

Return ONLY valid JSON matching this exact structure:
{{
  "questions": [
    {{
      "question_text": "...",
      "options": [
        {{"id": "A", "label": "A", "text": "..."}},
        {{"id": "B", "label": "B", "text": "..."}},
        {{"id": "C", "label": "C", "text": "..."}},
        {{"id": "D", "label": "D", "text": "..."}}
      ],
      "correct_option_ids": ["A"],
      "explanation": "..." or null,
      "needs_explanation_review": false,
      "chapter": "exact chapter string from the list, or null" or null,
      "complexity": "easy" or "medium" or "hard"
    }}
  ],
  "detected_answer_format": "A/B/C/D",
  "extraction_notes": "..."
}}"""

def _extract_question_text(q_data: dict) -> str:
    """Return question text regardless of which key the AI used."""
    for key in ("question_text", "question", "q", "text", "stem", "question_stem", "problem"):
        val = q_data.get(key)
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    # Log the actual keys returned so mismatches are visible in worker logs
    logger.warning("Could not extract question_text. Actual keys in q_data: %s", list(q_data.keys()))
    return ""


def _require_questions_list(result: object) -> list:
    """Validate the AI returned the expected `{"questions": [...]}` shape.

    Raises AIResponseError (→ job fails with a readable message) instead of
    silently coercing bad output into an empty list, which previously produced
    a 'completed' job with zero questions and no explanation.
    """
    if not isinstance(result, dict):
        raise AIResponseError("model response was not a JSON object")
    questions = result.get("questions")
    if not isinstance(questions, list):
        raise AIResponseError("model response did not include a 'questions' list")
    return questions


def _align_replacements(
    rejected_ids: list, questions_data: list,
) -> tuple[list[tuple], list[str]]:
    """Match regenerated replacements to the rejected questions they fix.

    Primary key is the echoed "replaces_ref" ("R<n>", 1-based over the prompt order).
    Positional order is trusted ONLY when no item echoed a ref AND the counts match
    exactly — a count/order mismatch must never silently overwrite the wrong question.
    Returns (matched [(question_id, payload)], skip_reasons).
    """
    skipped: list[str] = []
    ref_to_qid = {f"R{i + 1}": qid for i, qid in enumerate(rejected_ids)}

    refs: list[str | None] = []
    for item in questions_data:
        ref = item.get("replaces_ref") if isinstance(item, dict) else None
        refs.append(str(ref).strip().upper() if isinstance(ref, (str, int)) and str(ref).strip() else None)

    if not any(refs):
        if len(questions_data) == len(rejected_ids):
            return list(zip(rejected_ids, questions_data)), skipped
        skipped.append(
            f"no replaces_ref echoed and count mismatch ({len(questions_data)} returned "
            f"for {len(rejected_ids)} rejected) — cannot align safely"
        )
        return [], skipped

    matched: list[tuple] = []
    seen: set[str] = set()
    for ref, item in zip(refs, questions_data):
        if ref is None:
            skipped.append("replacement missing replaces_ref")
            continue
        qid = ref_to_qid.get(ref)
        if qid is None:
            skipped.append(f"unknown replaces_ref '{ref}'")
            continue
        if ref in seen:
            skipped.append(f"duplicate replaces_ref '{ref}'")
            continue
        seen.add(ref)
        matched.append((qid, item))
    return matched, skipped


def _normalize_question(q_data: object) -> tuple[dict | None, str | None]:
    """Validate and normalize one question entry.

    Returns (fields, None) when usable, or (None, reason) when it must be
    skipped. The reason is surfaced to the admin via the job's output_reference
    so skipped questions are visible, not silently dropped.
    """
    if not isinstance(q_data, dict):
        return None, "entry was not an object"
    question_text = _extract_question_text(q_data)
    if not question_text:
        return None, "missing question text"
    options = normalize_options(q_data.get("options", []))
    if len(options) < 4:
        return None, f"only {len(options)} option(s) after normalization"
    correct = q_data.get("correct_option_ids") or []
    if not isinstance(correct, list) or not correct:
        return None, "missing correct answer"
    complexity = q_data.get("complexity", "medium")
    if complexity not in _VALID_COMPLEXITY:
        complexity = "medium"
    return {
        "question_text": question_text,
        "options": options,
        "correct_option_ids": correct,
        "explanation": q_data.get("explanation"),
        # `chapter` is present on extraction output (auto-detect mode); `topic`/`subtopic`
        # on generation/regeneration output. Each is None when the agent didn't emit it.
        "chapter": q_data.get("chapter"),
        "topic": q_data.get("topic"),
        "subtopic": q_data.get("subtopic"),
        "complexity": complexity,
    }, None


def _normalize_batch(questions_data: list) -> tuple[list[dict], list[str]]:
    """Split a raw question list into (usable fields, skip reasons)."""
    normalized: list[dict] = []
    skipped: list[str] = []
    for q_data in questions_data:
        fields, reason = _normalize_question(q_data)
        if fields is None:
            skipped.append(reason or "invalid")
        else:
            normalized.append(fields)
    return normalized, skipped


_OPTION_IDS = ["A", "B", "C", "D"]


def _q_get(q, key):
    return q[key] if isinstance(q, dict) else getattr(q, key)


def _q_set(q, key, value) -> None:
    if isinstance(q, dict):
        q[key] = value
    else:
        setattr(q, key, value)


def balance_answer_positions(questions: list) -> None:
    """Spread the correct-answer position evenly over A/B/C/D across a batch.

    LLMs strongly bias the correct option toward the first position, so prompt
    instructions alone do not give a uniform distribution. This deterministically
    re-orders each question's options so the correct answer lands on a balanced,
    randomised set of positions (equal counts of A/B/C/D, shuffled order). Only the
    option ORDER and A/B/C/D labels change — the option texts and which text is
    correct are untouched, and `correct_option_ids` is updated to the new label.

    Works on both dict records (extraction/generation) and ORM MCQQuestion rows
    (regeneration). Only standard single-correct, 4-distinct-option questions are
    touched; anything unusual is left exactly as produced. Explanations must refer
    to the answer by content, not letter (enforced in the prompts), so re-labelling
    is safe.
    """
    eligible: list = []
    for q in questions:
        opts = _q_get(q, "options") or []
        correct = _q_get(q, "correct_option_ids") or []
        if len(opts) != 4 or len(correct) != 1:
            continue
        ids = [o.get("id") for o in opts if isinstance(o, dict)]
        if len(ids) == 4 and len(set(ids)) == 4 and correct[0] in ids:
            eligible.append(q)

    if not eligible:
        return

    # Balanced target positions (each of 0..3 appears ~n/4 times), randomised order.
    targets = [i % 4 for i in range(len(eligible))]
    random.shuffle(targets)

    for q, target in zip(eligible, targets):
        opts = _q_get(q, "options")
        correct_id = _q_get(q, "correct_option_ids")[0]
        correct_opt = next(o for o in opts if o.get("id") == correct_id)
        distractors = [o for o in opts if o.get("id") != correct_id]

        ordered = list(distractors)
        ordered.insert(target, correct_opt)  # place the correct option at the target slot

        new_opts = [
            {"id": _OPTION_IDS[pos], "label": _OPTION_IDS[pos], "text": str(o.get("text", ""))}
            for pos, o in enumerate(ordered)
        ]
        _q_set(q, "options", new_opts)
        _q_set(q, "correct_option_ids", [_OPTION_IDS[target]])


def _extraction_output(
    saved: int, skipped: list[str], total_returned: int, batch_id,
    layout: dict | None = None,
    chapters_detected: list[str] | None = None,
    questions_by_chapter: dict[str, int] | None = None,
    unassigned_count: int | None = None,
) -> dict:
    """Job output_reference so the admin can see how many questions were saved
    vs. skipped (and why), instead of silently losing malformed entries.

    `layout` carries the per-page column-split breakdown from the OCR pass (CLAUDE.md §8),
    mirroring what the knowledge job records — without it the slicing decisions are invisible
    once the transient progress step is overwritten. `chapters_detected` /
    `questions_by_chapter` / `unassigned_count` (extraction only) surface the auto-detect
    chapter distribution so the admin can verify assignment and spot unfiled questions."""
    out: dict = {
        "batch_id": str(batch_id),
        "saved": saved,
        "skipped": len(skipped),
        "total_returned": total_returned,
    }
    if skipped:
        out["skip_reasons"] = skipped[:20]
    if layout:
        out["layout"] = layout
    if chapters_detected is not None:
        out["chapters_detected"] = chapters_detected
    if questions_by_chapter is not None:
        out["questions_by_chapter"] = questions_by_chapter
    if unassigned_count is not None:
        out["unassigned_count"] = unassigned_count
    return out



# ── Example questions (the primary style/inspiration input for generation) ────────────
# MCQ generation/regeneration take their CONTENT from the source document; approved example
# questions of the same topics show the model what a real Loksewa question LOOKS like —
# framing, difficulty calibration, and distractor craft. This replaced the old knowledge-chunk
# enrichment + topic-detection call (the source already carries the facts; notes were marginal).

EXAMPLE_QUESTIONS_LIMIT = 12
_COMPLEXITY_ORDER = ("easy", "medium", "hard")


def _select_diverse(pool: list, limit: int = EXAMPLE_QUESTIONS_LIMIT) -> list:
    """Pick up to `limit` example questions SPREAD across easy/medium/hard.

    The model learns question FORM from these, so a difficulty mix teaches the full framing
    range instead of over-weighting whatever the bank holds most of. Round-robins the three
    complexity buckets (preserving each bucket's incoming order), then tops up from anything
    left (e.g. untagged complexities). Pure — unit-tested."""
    if limit <= 0 or not pool:
        return []
    buckets: dict[str, list] = {c: [] for c in _COMPLEXITY_ORDER}
    other: list = []
    for q in pool:
        c = _q_get(q, "complexity")
        (buckets[c] if c in buckets else other).append(q)

    picked: list = []
    idx = {c: 0 for c in _COMPLEXITY_ORDER}
    while len(picked) < limit:
        advanced = False
        for c in _COMPLEXITY_ORDER:
            if idx[c] < len(buckets[c]):
                picked.append(buckets[c][idx[c]])
                idx[c] += 1
                advanced = True
                if len(picked) >= limit:
                    break
        if not advanced:
            break
    if len(picked) < limit and other:
        picked.extend(other[: limit - len(picked)])
    return picked


async def _fetch_example_questions(
    db: AsyncSession, *, exam_id, chapter: str | None, topic: str | None,
    limit: int = EXAMPLE_QUESTIONS_LIMIT,
) -> list:
    """Approved example questions of these topics — the primary quality lever for generation
    now that content comes from the source document. Scoped CHAPTER-first (topic narrows), with
    graceful fallback chapter+topic → chapter → exam so a thin bank still yields examples.
    Returns a complexity-diversified list (see `_select_diverse`)."""
    from sqlalchemy import select

    base = [MCQQuestion.exam_id == exam_id, MCQQuestion.status == "approved"]
    tiers: list[list] = []
    if chapter and topic:
        tiers.append(base + [MCQQuestion.chapter == chapter, MCQQuestion.topic == topic])
    if chapter:
        tiers.append(base + [MCQQuestion.chapter == chapter])
    tiers.append(base)

    pool: list = []
    seen: set = set()
    for where in tiers:
        rows = (await db.execute(
            select(MCQQuestion).where(*where)
            .order_by(MCQQuestion.created_at.desc()).limit(limit * 3)
        )).scalars().all()
        for q in rows:
            if q.id not in seen:
                seen.add(q.id)
                pool.append(q)
        # Stop broadening once the narrower tiers already give a healthy pool.
        if len(pool) >= limit * 2:
            break
    return _select_diverse(pool, limit)


def _format_example_questions(examples: list) -> str:
    """Render example questions (Q + options with ✓ correct + explanation) for the prompt.
    Empty → cold-start guidance so a fresh exam with no approved bank degrades gracefully."""
    if not examples:
        return ("No approved example questions yet — write diverse, well-structured questions in "
                "authentic Loksewa style using the DISTRACTOR DESIGN rules above.")
    lines: list[str] = []
    for ex in examples:
        correct_ids = _q_get(ex, "correct_option_ids") or []
        lines.append(f"Q: {_q_get(ex, 'question_text')}")
        for opt in (_q_get(ex, "options") or []):
            mark = "✓" if opt.get("id") in correct_ids else " "
            lines.append(f"  [{mark}] {opt.get('id')}. {opt.get('text')}")
        lines.append(f"  Explanation: {_q_get(ex, 'explanation') or 'N/A'}")
        lines.append("")
    return "\n".join(lines)

GENERATION_PROMPT = EXAM_CONTEXT + """

ROLE: You are an expert Loksewa/banking exam question writer. You author original,
exam-quality MCQs that a real Public Service Commission paper-setter would be proud of —
testing genuine understanding, not trivia.

TASK: Write {count} original multiple-choice questions grounded in the SOURCE DOCUMENT below.

HARD RULES (never violate):
- GROUNDING: Build questions only from facts in the SOURCE DOCUMENT — it is the SOLE source of
  content. The EXAMPLE QUESTIONS are style/inspiration ONLY: mirror their framing, difficulty, and
  distractor craft, but NEVER copy their content or build a question from an example instead of
  the source.
- SELF-CONTAINED — NEVER REFERENCE THE SOURCE: every question must read as a standalone exam
  question answerable from subject knowledge alone. Forbidden are phrases like "according to
  the document", "as per the text", "स्रोत दस्तावेजअनुसार", "दिइएको अनुच्छेदअनुसार", "passage मा
  उल्लेख भएअनुसार", "उपरोक्त सामग्रीअनुसार", or any reference to a document/passage/text/material.
  Write "What is X?" — never "According to the document, what is X?".
- Each question has exactly 4 options (A, B, C, D), exactly one correct, plus a clear explanation.
- No duplicate or near-duplicate stems; spread difficulty across easy/medium/hard.

DISTRACTOR DESIGN (this is what makes or breaks question quality — treat it as critical):
- The 3 wrong options must be EXPERT TRAPS, deliberately chosen to confuse a half-prepared student,
  exactly as a human Loksewa paper-setter would design them. They must be the SAME TYPE, scale, and
  format as the correct answer, and individually plausible.
- For NUMERICAL/quantity answers, distractors must be NEAR the correct value and look like real
  competing figures — e.g. the previously-quoted official value, a common rounding, transposed or
  off-by-one digits, or a related statistic. Keep identical units and number formatting.
    · Worked example (study the pattern, do NOT reuse this content): for "नेपालको कुल क्षेत्रफल कति हो?"
      the key is 1,47,516 वर्ग कि.मि.; strong distractors are 1,47,181 वर्ग कि.मि. (the older official
      figure), 1,48,516 वर्ग कि.मि., and 1,41,516 वर्ग कि.मि. — all close, same format, genuinely
      confusing. WEAK/forbidden: 50,000 वर्ग कि.मि. or 9,84,000 वर्ग कि.मि. (obviously wrong by scale).
- For CONCEPT/term answers, use closely-related terms, adjacent categories, common misconceptions, or
  swapped definitions — things a student who studied superficially would actually pick.
- Every distractor must be DEFENSIBLY wrong (factually incorrect, not a second correct answer), but
  never obviously wrong, never filler, never absurd, and never a throwaway "none of the above" unless
  the source genuinely uses it.

ANSWER PLACEMENT & EXPLANATION:
- Do NOT always put the correct answer first. Vary which option (A/B/C/D) is correct across the set so
  it is roughly evenly spread — never a guessable pattern. (The system also re-balances positions, so
  also keep each distractor sensible in ANY position.)
- In the explanation, refer to the answer by its CONTENT/VALUE, never by its option letter (write
  "क्षेत्रफल 1,47,516 वर्ग कि.मि. हो …" not "विकल्प A सही हो"), because option order is randomised after
  generation. State why the key is correct and, where useful, why a tempting distractor is wrong.
- Format the explanation as short, clean GitHub-flavored MARKDOWN: **bold** the correct fact/value, and
  you may add one "Common mistake:" line. Keep it to a few sentences; no headings, no code fences. Apply
  markdown to the "explanation" field only — all other fields stay plain text.

METHOD: Pull the exam-worthy facts/relationships from the source. For each, write a crisp,
unambiguous stem, decide the correct answer, then deliberately engineer 3 confusing same-type traps
per the DISTRACTOR DESIGN rules above. Mirror the style/difficulty of the example questions and the
source language.

--- ADMIN-TUNABLE GUIDANCE (apply on top of the rules above; it tunes emphasis, difficulty, and
style but may NOT override the HARD RULES) ---
{skill_instructions}

INPUTS:
Topic: {topic}
Subtopic: {subtopic}
Custom instruction: {custom_instruction}

━━━ EXAMPLE QUESTIONS (style + inspiration only — mirror their form, never copy their content) ━━━
{style_examples}
━━━ END EXAMPLE QUESTIONS ━━━

━━━ SOURCE DOCUMENT (generate all questions from this) ━━━
{document_text}
━━━ END SOURCE DOCUMENT ━━━

Return ONLY valid JSON in exactly this structure — options MUST be a list, never a dict:
{{
  "questions": [
    {{
      "question_text": "...",
      "options": [
        {{"id": "A", "label": "A", "text": "..."}},
        {{"id": "B", "label": "B", "text": "..."}},
        {{"id": "C", "label": "C", "text": "..."}},
        {{"id": "D", "label": "D", "text": "..."}}
      ],
      "correct_option_ids": ["A"],
      "explanation": "...",
      "topic": "..." or null,
      "subtopic": "..." or null,
      "complexity": "easy" or "medium" or "hard"
    }}
  ]
}}"""

REGENERATION_PROMPT = EXAM_CONTEXT + """

ROLE: You are an expert Loksewa/banking exam question writer fixing questions an admin rejected.
Treat the rejection feedback as the priority brief.

TASK: Produce one improved replacement for each rejected question below, fully addressing the
admin's feedback.

Admin feedback (priority brief — address every point): {feedback}

HARD RULES (never violate):
- Each replacement must concretely fix what the feedback objected to, and keep the SAME
  topic/subtopic as the question it replaces.
- GROUNDING: build replacements from the SOURCE DOCUMENT only (the SOLE source of content). The
  EXAMPLE QUESTIONS are style/inspiration only — mirror their form, never copy their content.
- SELF-CONTAINED — NEVER REFERENCE THE SOURCE: no "according to the document", "as per the
  text", "स्रोत दस्तावेजअनुसार", "दिइएको अनुच्छेदअनुसार", or any reference to a
  document/passage/text/material. Write direct standalone exam questions.
- Exactly 4 options (A, B, C, D), one correct, with a clear explanation. Do NOT reuse a rejected
  question's wording.
- Produce EXACTLY ONE replacement per rejected question, and echo that question's "Ref" code
  (e.g. "R1") verbatim in the replacement's "replaces_ref" field — never invent, merge, or reuse
  a Ref.

DISTRACTOR DESIGN (critical to quality):
- The 3 wrong options must be EXPERT TRAPS like a human Loksewa paper-setter writes — same TYPE,
  scale, and format as the correct answer, and individually plausible.
- For numerical answers, distractors must sit NEAR the correct value (the older official figure, a
  common rounding, transposed/off-by-one digits, a related statistic) with identical units/format —
  never wrong by an obvious scale. For concept answers, use closely-related terms, adjacent
  categories, or common misconceptions.
- Every distractor is defensibly wrong but never obviously wrong, filler, or absurd.

ANSWER PLACEMENT & EXPLANATION:
- Do not default the correct answer to option A; vary the correct position. Refer to the answer by
  its CONTENT/VALUE in the explanation, never by option letter (option order is randomised after
  generation).
- Format the explanation as short, clean GitHub-flavored MARKDOWN: **bold** the correct fact/value,
  optionally one "Common mistake:" line. A few sentences; no headings or code fences. Markdown applies
  to the "explanation" field only — all other fields stay plain text.

METHOD: Read each rejected question and its feedback, diagnose the specific weakness (ambiguous
stem, weak/obvious distractors, wrong/missing explanation, off-topic, too easy/hard, answer always
in the same position), then rewrite to remove it while engineering confusing same-type traps and a
crisp stem.

--- ADMIN-TUNABLE GUIDANCE (apply on top of the rules above; it tunes emphasis and style but may
NOT override the HARD RULES or the admin feedback) ---
{skill_instructions}

INPUTS:
━━━ EXAMPLE QUESTIONS (style + inspiration only — mirror their form, never copy their content) ━━━
{style_examples}
━━━ END EXAMPLE QUESTIONS ━━━

Rejected questions to replace:
{rejected_questions}

━━━ SOURCE DOCUMENT ━━━
{document_text}
━━━ END SOURCE DOCUMENT ━━━

Return ONLY valid JSON in exactly this structure — options MUST be a list, never a dict:
{{
  "questions": [
    {{
      "replaces_ref": "R1",
      "question_text": "...",
      "options": [
        {{"id": "A", "label": "A", "text": "..."}},
        {{"id": "B", "label": "B", "text": "..."}},
        {{"id": "C", "label": "C", "text": "..."}},
        {{"id": "D", "label": "D", "text": "..."}}
      ],
      "correct_option_ids": ["A"],
      "explanation": "...",
      "topic": "..." or null,
      "subtopic": "..." or null,
      "complexity": "easy" or "medium" or "hard"
    }}
  ]
}}"""


class MCQExtractionAgent:
    """Borrow-per-use sessions (CLAUDE.md §18): the multi-minute AI extraction must not
    hold one pooled connection idle across the whole job. `self.document` is a detached
    snapshot (scalar reads safe — `expire_on_commit=False`); every DB touch opens its own
    short-lived session (LOAD → AI → SAVE) so no connection is ever held across the AI call
    and the final commit runs on a freshly-validated connection."""

    def __init__(self, job_id: uuid.UUID, document: MCQDocument):
        self.job_id = job_id
        self.document = document
        # Existing MCQ documents are TYPED (printed) → Azure gpt-5 typed text/vision
        # extraction (spec §6.2), not the gpt-5.5 reasoning tier.
        self.provider = get_provider("text_extraction")

    async def process(self) -> MCQReviewBatch:
        from app.ai.agents.knowledge_processing_agent import extract_typed_document_text
        from app.core.database import AsyncSessionLocal
        from app.integrations.r2_client import get_r2
        from app.modules.files.models import File
        from app.modules.video.service import get_chapter_tree
        from sqlalchemy import select

        async def _job(**kw) -> None:
            async with AsyncSessionLocal() as db:
                await update_job(db, self.job_id, **kw)

        async def _ocr_step(progress: int, msg: str) -> None:
            await _job(progress=progress, step=msg)

        await _job(progress=5, step="Downloading source document")

        # LOAD: file record (short session) → snapshot the scalars we need.
        async with AsyncSessionLocal() as db:
            file_record = (await db.execute(
                select(File).where(File.id == self.document.file_id)
            )).scalar_one_or_none()
            if not file_record:
                raise ValueError("Source file not found")
            r2_key, mime_type = file_record.r2_key, file_record.mime_type

        file_bytes = await asyncio.to_thread(get_r2().download_fileobj, r2_key)

        await _job(progress=20, step="Extracting text from document")
        # Layout-aware extraction (CLAUDE.md §8/§9): a clean-Unicode text layer is used
        # for free; scanned/Preeti pages are column-aware vision-OCR'd instead of feeding
        # the model ASCII garbage. Raises when nothing could be extracted (fail honestly).
        document_text, layout_stats = await extract_typed_document_text(
            file_bytes, mime_type, _ocr_step,
            audit_ctx={
                "agent_type": "PageLayoutClassifier",
                "task_type": "page_layout_classification",
                "entity_type": "mcq_document",
                "entity_id": self.document.id,
            },
        )

        # Chapter is optional (CLAUDE.md §9.1): a chosen chapter LOCKS every question to it;
        # blank ⇒ the extractor assigns chapter per question, then per-chapter topic stage.
        locked_chapter = (self.document.chapter or "").strip() or None

        await _job(progress=33, step="Retrieving active skill + syllabus")
        async with AsyncSessionLocal() as db:
            skill_instructions = await self._get_skill(db)
            (full_tree_text, valid_topics, valid_subtopics,
             valid_chapters, topic_to_chapter) = await get_chapter_tree(db, self.document.exam_id)

        # ── Stage B: extraction per ≤40k chunk, IN PARALLEL (chapter-only, no topics) ──────
        chunks = _split_text_chunks(document_text)
        chapter_block = (
            "" if locked_chapter
            else CHAPTER_ASSIGNMENT_BLOCK.format(chapter_list=_build_chapter_list(valid_chapters))
        )
        await _job(progress=45, step=f"Extracting MCQs with AI ({len(chunks)} chunk(s))")
        normalized, skipped, total_returned = await self._extract_chunks(
            chunks, skill_instructions, chapter_block
        )

        # ── Resolve chapter per question + group (chapter is PRIMARY) ──────────────────────
        # locked ⇒ everything under the locked chapter; auto ⇒ the AI chapter validated
        # against the real chapter list (unknown/absent ⇒ the null recovery group).
        groups: dict[str | None, list[dict]] = {}
        for f in normalized:
            if locked_chapter:
                key: str | None = locked_chapter
            else:
                ai_ch = f.get("chapter")
                ai_ch = ai_ch.strip() if isinstance(ai_ch, str) else ""
                key = ai_ch if (ai_ch and ai_ch in valid_chapters) else None
            groups.setdefault(key, []).append(f)

        # ── Stage C: assign topic/subtopic per chapter (parallel), recover the null group ──
        await _job(progress=70, step="Assigning topics within each chapter")
        await self._assign_topics(
            groups, locked_chapter=locked_chapter, full_tree_text=full_tree_text,
            valid_topics=valid_topics, valid_subtopics=valid_subtopics,
            valid_chapters=valid_chapters, topic_to_chapter=topic_to_chapter,
        )

        # ── Chapter distribution for the job output ────────────────────────────────────────
        by_chapter: dict[str, int] = {}
        unassigned = 0
        for f in normalized:
            if f.get("chapter"):
                by_chapter[f["chapter"]] = by_chapter.get(f["chapter"], 0) + 1
            else:
                unassigned += 1

        await _job(progress=88, step="Saving extracted questions")
        # SAVE: re-load the document in a fresh session; batch + questions + doc update commit
        # together as one transaction on a freshly-validated connection.
        async with AsyncSessionLocal() as db:
            doc = (await db.execute(
                select(MCQDocument).where(MCQDocument.id == self.document.id)
            )).scalar_one()
            await _clear_prior_batches(db, self.job_id)
            batch = MCQReviewBatch(
                document_id=doc.id, batch_type="extraction", status="in_review",
                total_questions=len(normalized), job_id=self.job_id, created_by=doc.created_by,
            )
            db.add(batch)
            await db.flush()
            for f in normalized:
                db.add(MCQQuestion(
                    source_document_id=doc.id, review_batch_id=batch.id, exam_id=doc.exam_id,
                    origin_type="uploaded_extracted", question_text=f["question_text"],
                    options=f["options"], correct_option_ids=f["correct_option_ids"],
                    explanation=f["explanation"], chapter=f.get("chapter"),
                    topic=f.get("topic"), subtopic=f.get("subtopic"),
                    complexity=f["complexity"], status="draft",
                ))
            doc.processing_status = "completed"
            doc.question_count = len(normalized)
            await db.commit()

        await _job(progress=100, step="Extraction complete",
                   output=_extraction_output(
                       len(normalized), skipped, total_returned, batch.id,
                       layout=layout_stats,
                       chapters_detected=sorted(by_chapter.keys()),
                       questions_by_chapter=by_chapter,
                       unassigned_count=unassigned,
                   ))
        return batch

    async def _extract_chunks(
        self, chunks: list[str], skill_instructions: str, chapter_block: str,
    ) -> tuple[list[dict], list[str], int]:
        """Run the extraction call once per text chunk IN PARALLEL (Semaphore(6), each on
        its own short session for audit). Partial success: a chunk that fails contributes
        nothing and is logged; only ALL chunks failing raises. Returns
        (normalized questions, skip reasons, total questions returned by the model)."""
        from app.core.database import AsyncSessionLocal

        if not chunks:
            return [], [], 0

        sem = asyncio.Semaphore(6)
        failures = 0

        async def _one(idx: int, chunk_text: str) -> list:
            nonlocal failures
            prompt = EXTRACTION_PROMPT.format(
                chapter_assignment_block=chapter_block,
                skill_instructions=skill_instructions,
                custom_instruction=self.document.custom_instruction or "none",
                document_text=chunk_text,
            )
            async with sem:
                async with AsyncSessionLocal() as db:
                    audit_ctx = {
                        "db": db, "agent_type": "MCQExtractionAgent",
                        "task_type": "mcq_extraction",
                        "entity_type": "mcq_document", "entity_id": self.document.id,
                    }
                    try:
                        result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
                        return _require_questions_list(result)
                    except Exception as exc:  # noqa: BLE001
                        failures += 1
                        logger.warning(
                            "Extraction chunk %d/%d failed: %s", idx + 1, len(chunks), exc
                        )
                        return []

        results = await asyncio.gather(*[_one(i, c) for i, c in enumerate(chunks)])
        if failures == len(chunks):
            raise RuntimeError("AI extraction failed on every chunk of the document")

        questions_data: list = []
        for r in results:
            questions_data.extend(r)
        normalized, skipped = _normalize_batch(questions_data)
        if failures:
            skipped.append(f"{failures} chunk(s) failed extraction and were skipped")
        return normalized, skipped, len(questions_data)

    async def _assign_topics(
        self, groups: dict, *, locked_chapter: str | None, full_tree_text: str,
        valid_topics: set, valid_subtopics: set, valid_chapters: set, topic_to_chapter: dict,
    ) -> None:
        """Stage C — assign topic/subtopic to each question, writing the FINAL validated
        (chapter, topic, subtopic) back onto each question dict in place.

        One task per chapter group (bounded by Semaphore(6)); within a group, 25-question
        chunks run sequentially. A real-chapter group gets the scoped tree (that chapter's
        topics only); the null group gets a recovery pass over the full tree that may also
        assign a chapter. Every label is validated by `resolve_syllabus_labels` — the AI only
        proposes. A Stage-C failure leaves the group's questions with null topic (chapter for
        a real group still stands), never a wrong label."""
        from app.core.database import AsyncSessionLocal
        from app.modules.video.service import get_chapter_tree, resolve_syllabus_labels
        from app.ai.agents.mcq_topic_assignment_agent import MCQTopicAssignmentAgent

        CHUNK = 25

        def _finalize(q: dict, *, chapter_in, topic_in, subtopic_in) -> None:
            t, sub, ch = resolve_syllabus_labels(
                topic=topic_in, subtopic=subtopic_in, chapter=chapter_in,
                valid_topics=valid_topics, valid_subtopics=valid_subtopics,
                valid_chapters=valid_chapters, topic_to_chapter=topic_to_chapter,
                locked_chapter=locked_chapter,
            )
            # Locked mode: if Stage C found no topic, fall back to the admin's topic hint
            # (validated) — preserves the pre-two-stage behaviour where doc.topic seeded it.
            if locked_chapter and not t and self.document.topic:
                t2, sub2, _ = resolve_syllabus_labels(
                    topic=self.document.topic, subtopic=self.document.subtopic,
                    chapter=locked_chapter, valid_topics=valid_topics,
                    valid_subtopics=valid_subtopics, valid_chapters=valid_chapters,
                    topic_to_chapter=topic_to_chapter, locked_chapter=locked_chapter,
                )
                t, sub = t or t2, sub or sub2
            q["chapter"], q["topic"], q["subtopic"] = ch, t, sub

        sem = asyncio.Semaphore(6)

        async def _process_group(group_chapter: str | None, qs: list[dict]) -> None:
            async with sem:
                scoped_tree = ""
                if group_chapter is not None:
                    async with AsyncSessionLocal() as db:
                        scoped_tree, *_ = await get_chapter_tree(
                            db, self.document.exam_id, chapter=group_chapter
                        )
                for start in range(0, len(qs), CHUNK):
                    sub = qs[start:start + CHUNK]
                    async with AsyncSessionLocal() as db:
                        agent = MCQTopicAssignmentAgent(db, entity_id=self.document.id)
                        try:
                            if group_chapter is not None:
                                assigned = await agent.assign_scoped(
                                    chapter=group_chapter, topic_tree=scoped_tree, questions=sub,
                                )
                            else:
                                assigned = await agent.assign_recovery(
                                    tree_text=full_tree_text, questions=sub,
                                )
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "Stage C topic assignment failed (chapter=%s): %s",
                                group_chapter, exc,
                            )
                            assigned = {}
                    for i, q in enumerate(sub):
                        a = assigned.get(i) or {}
                        if group_chapter is not None:
                            _finalize(q, chapter_in=group_chapter,
                                      topic_in=a.get("topic"), subtopic_in=a.get("subtopic"))
                        else:
                            _finalize(q, chapter_in=a.get("chapter"),
                                      topic_in=a.get("topic"), subtopic_in=a.get("subtopic"))

        await asyncio.gather(*[_process_group(k, v) for k, v in groups.items()])

    async def _get_skill(self, db: AsyncSession) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(db, "MCQExtractionAgent")
        except Exception:
            return "Favour exact fidelity over tidiness; when the answer key is ambiguous, mark needs_explanation_review rather than guessing."


class MCQGenerationAgent:
    """Borrow-per-use sessions (CLAUDE.md §18) — same rationale as MCQExtractionAgent:
    the example-question fetch and the multi-minute generation AI call each run on their own
    short-lived session so no pooled connection is held idle across the job. (Generation reads
    NO knowledge layer and runs a single AI call — §9.2.)"""

    def __init__(self, job_id: uuid.UUID, document: MCQDocument, count: int):
        self.job_id = job_id
        self.document = document
        self.count = count
        self.provider = get_provider("reasoning")

    async def process(self) -> MCQReviewBatch:
        from app.ai.agents.knowledge_processing_agent import extract_typed_document_text
        from app.core.database import AsyncSessionLocal
        from app.integrations.r2_client import get_r2
        from app.modules.files.models import File
        from sqlalchemy import select

        async def _job(**kw) -> None:
            async with AsyncSessionLocal() as db:
                await update_job(db, self.job_id, **kw)

        async def _ocr_step(progress: int, msg: str) -> None:
            await _job(progress=progress, step=msg)

        await _job(progress=5, step="Downloading source content")

        # LOAD: file record (short session) → snapshot the scalars we need.
        async with AsyncSessionLocal() as db:
            file_record = (await db.execute(
                select(File).where(File.id == self.document.file_id)
            )).scalar_one_or_none()
            if not file_record:
                raise ValueError("Source file not found")
            r2_key, mime_type = file_record.r2_key, file_record.mime_type

        file_bytes = await asyncio.to_thread(get_r2().download_fileobj, r2_key)

        await _job(progress=20, step="Extracting text from content")
        # Layout-aware extraction — the file IS the requested source content, so a totally
        # unreadable file fails the job honestly (see extract_typed_document_text).
        document_text, layout_stats = await extract_typed_document_text(
            file_bytes, mime_type, _ocr_step,
            audit_ctx={
                "agent_type": "PageLayoutClassifier",
                "task_type": "page_layout_classification",
                "entity_type": "mcq_document",
                "entity_id": self.document.id,
            },
        )

        await _job(progress=45, step="Loading example questions")
        async with AsyncSessionLocal() as db:
            examples = await _fetch_example_questions(
                db, exam_id=self.document.exam_id,
                chapter=self.document.chapter, topic=self.document.topic,
            )
            style_examples = _format_example_questions(examples)

        await _job(progress=60, step="Retrieving active skill")
        async with AsyncSessionLocal() as db:
            skill_instructions = await self._get_skill(db)

        await _job(progress=68, step="Generating MCQs with AI")
        prompt = GENERATION_PROMPT.format(
            count=self.count,
            skill_instructions=skill_instructions,
            topic=self.document.topic or "general",
            subtopic=self.document.subtopic or "general",
            custom_instruction=self.document.custom_instruction or "none",
            style_examples=style_examples,
            document_text=document_text[:30000],
        )

        # AI call on its OWN short session (held only for this one call + its audit write).
        async with AsyncSessionLocal() as db:
            audit_ctx = {
                "db": db, "agent_type": "MCQGenerationAgent", "task_type": "mcq_generation",
                "entity_type": "mcq_document", "entity_id": self.document.id,
            }
            try:
                result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
            except Exception as exc:
                raise RuntimeError(f"AI generation failed: {exc}") from exc

        await _job(progress=88, step="Saving generated questions")
        questions_data = _require_questions_list(result)
        normalized, skipped = _normalize_batch(questions_data)

        # A generation request explicitly asked for N questions; producing none
        # usable is a failure, not a silently-completed empty batch.
        if not normalized:
            raise AIResponseError(
                f"generation produced no usable questions out of {len(questions_data)} returned"
            )

        # Spread the correct option evenly across A/B/C/D (LLMs cluster it on A).
        balance_answer_positions(normalized)

        # SAVE: re-load the document in a fresh session; commit batch + questions + doc update.
        async with AsyncSessionLocal() as db:
            doc = (await db.execute(
                select(MCQDocument).where(MCQDocument.id == self.document.id)
            )).scalar_one()
            await _clear_prior_batches(db, self.job_id)
            batch = MCQReviewBatch(
                document_id=doc.id, batch_type="generation", status="in_review",
                total_questions=len(normalized), job_id=self.job_id, created_by=doc.created_by,
            )
            db.add(batch)
            await db.flush()
            for f in normalized:
                db.add(MCQQuestion(
                    source_document_id=doc.id, review_batch_id=batch.id, exam_id=doc.exam_id,
                    origin_type="ai_generated", question_text=f["question_text"],
                    options=f["options"], correct_option_ids=f["correct_option_ids"],
                    explanation=f["explanation"], chapter=doc.chapter,
                    topic=f["topic"] or doc.topic, subtopic=f["subtopic"] or doc.subtopic,
                    complexity=f["complexity"], status="draft",
                ))
            doc.processing_status = "completed"
            doc.question_count = len(normalized)
            await db.commit()

        await _job(progress=100, step="Generation complete",
                   output=_extraction_output(len(normalized), skipped, len(questions_data), batch.id,
                                             layout=layout_stats))
        return batch

    async def _get_skill(self, db: AsyncSession) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(db, "MCQGenerationAgent")
        except Exception:
            return "Favour application over rote recall; make every distractor a plausible Loksewa-style trap, not filler."


class MCQRegenerationAgent:
    """Borrow-per-use sessions (CLAUDE.md §18). Rejected questions are snapshotted to plain
    data in a LOAD session, the AI runs holding no long-lived connection, and the rows are
    re-loaded and mutated in a fresh SAVE session — so no connection is held across the
    multi-minute regeneration call."""

    def __init__(self, job_id: uuid.UUID, batch: MCQReviewBatch, feedback: str):
        self.job_id = job_id
        # Capture the batch UUID as a plain Python value (the ORM object is detached).
        self._batch_id: uuid.UUID = batch.id
        self.feedback = feedback
        self.provider = get_provider("reasoning")

    async def process(self) -> None:
        from app.ai.agents.knowledge_processing_agent import extract_typed_document_text
        from app.core.database import AsyncSessionLocal
        from app.integrations.r2_client import get_r2
        from app.modules.files.models import File
        from app.modules.jobs.models import JobStatus
        from sqlalchemy import select

        async def _job(**kw) -> None:
            async with AsyncSessionLocal() as db:
                await update_job(db, self.job_id, **kw)

        async def _ocr_step(progress: int, msg: str) -> None:
            await _job(progress=progress, step=msg)

        await _job(progress=10, step="Collecting rejected questions")

        # LOAD: snapshot rejected questions + the source doc's file/topic to plain data.
        # Deterministic order so the prompt order matches the SAVE re-load order.
        async with AsyncSessionLocal() as db:
            rejected_rows = (await db.execute(
                select(MCQQuestion)
                .where(MCQQuestion.review_batch_id == self._batch_id, MCQQuestion.status == "rejected")
                .order_by(MCQQuestion.created_at.asc(), MCQQuestion.id.asc())
            )).scalars().all()
            rejected = [{
                "id": q.id, "question_text": q.question_text, "topic": q.topic,
                "subtopic": q.subtopic, "review_feedback": q.review_feedback,
                "exam_id": q.exam_id, "chapter": q.chapter,
            } for q in rejected_rows]

            doc_topic: str | None = None
            file_meta: tuple[str, str] | None = None
            batch = (await db.execute(
                select(MCQReviewBatch).where(MCQReviewBatch.id == self._batch_id)
            )).scalar_one_or_none()
            if batch and batch.document_id:
                doc = (await db.execute(
                    select(MCQDocument).where(MCQDocument.id == batch.document_id)
                )).scalar_one_or_none()
                if doc:
                    doc_topic = doc.topic
                    if doc.file_id:
                        fr = (await db.execute(select(File).where(File.id == doc.file_id))).scalar_one_or_none()
                        if fr:
                            file_meta = (fr.r2_key, fr.mime_type)

        logger.info("Regeneration: batch_id=%s — found %d rejected question(s)", self._batch_id, len(rejected))

        if not rejected:
            logger.warning(
                "Regeneration: batch_id=%s has 0 rejected questions; "
                "they may have already been regenerated in a previous run.", self._batch_id,
            )
            await _job(status=JobStatus.completed, progress=100,
                       step="No rejected questions found — they may already have been regenerated")
            return

        await _job(progress=20, step="Loading source document")
        document_text = ""
        if file_meta:
            # The source document is OPTIONAL context for regeneration (the path already
            # tolerates a missing file), so an extraction failure degrades to
            # feedback+knowledge-only instead of failing the whole regeneration.
            try:
                file_bytes = await asyncio.to_thread(get_r2().download_fileobj, file_meta[0])
                extracted, _layout = await extract_typed_document_text(
                    file_bytes, file_meta[1], _ocr_step,
                    audit_ctx={
                        "agent_type": "PageLayoutClassifier",
                        "task_type": "page_layout_classification",
                        "entity_type": "mcq_review_batch",
                        "entity_id": self._batch_id,
                    },
                )
                document_text = extracted[:20000]
            except Exception as exc:
                logger.warning(
                    "Regeneration: could not extract source document text (%s) — "
                    "continuing without it", exc,
                )
                document_text = ""

        await _job(progress=45, step="Loading example questions")
        # Scope examples like generation: chapter-first (topic narrows). All rejected
        # questions in a batch share the source document's exam/chapter.
        ex_exam_id = next((r["exam_id"] for r in rejected if r.get("exam_id")), None)
        ex_chapter = next((r["chapter"] for r in rejected if r.get("chapter")), None)
        async with AsyncSessionLocal() as db:
            examples = await _fetch_example_questions(
                db, exam_id=ex_exam_id, chapter=ex_chapter, topic=doc_topic,
            )
            style_text = _format_example_questions(examples)
            skill_instructions = await self._get_skill(db)

        rejected_text = ""
        for i, q in enumerate(rejected):
            rejected_text += (
                f"Ref: R{i + 1}\nQuestion: {q['question_text']}\nTopic: {q['topic']}\n"
                f"Feedback: {q['review_feedback']}\n\n"
            )

        await _job(progress=55, step="Regenerating with AI")
        prompt = REGENERATION_PROMPT.format(
            feedback=self.feedback,
            skill_instructions=skill_instructions,
            style_examples=style_text,
            rejected_questions=rejected_text,
            document_text=document_text,
        )

        # AI call on its OWN short session (held only for this one call + its audit write).
        async with AsyncSessionLocal() as db:
            audit_ctx = {
                "db": db, "agent_type": "MCQRegenerationAgent", "task_type": "mcq_regeneration",
                "entity_type": "mcq_review_batch", "entity_id": self._batch_id,
            }
            try:
                result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
            except Exception as exc:
                raise RuntimeError(f"AI regeneration failed: {exc}") from exc

        await _job(progress=85, step="Replacing rejected questions")
        questions_data = _require_questions_list(result)

        # SAVE: re-load the rejected rows (same order) and apply replacements in a fresh session.
        async with AsyncSessionLocal() as db:
            rejected_ids = [r["id"] for r in rejected]
            rows_by_id = {q.id: q for q in (await db.execute(
                select(MCQQuestion).where(MCQQuestion.id.in_(rejected_ids))
            )).scalars().all()}

            aligned, skipped = _align_replacements(rejected_ids, questions_data)
            replaced = 0
            replaced_questions: list = []
            for qid, new_q_data in aligned:
                old_q = rows_by_id.get(qid)
                if old_q is None:
                    continue
                fields, reason = _normalize_question(new_q_data)
                if fields is None:
                    skipped.append(reason or "invalid")
                    continue
                old_q.question_text = fields["question_text"]
                old_q.options = fields["options"]
                old_q.correct_option_ids = fields["correct_option_ids"]
                old_q.explanation = fields["explanation"]
                old_q.complexity = fields["complexity"]
                old_q.status = "draft"
                old_q.review_feedback = None
                replaced += 1
                replaced_questions.append(old_q)

            if replaced == 0:
                raise AIResponseError(
                    f"regeneration produced no usable replacements out of {len(questions_data)} returned"
                )

            # Spread the correct option evenly across A/B/C/D (LLMs cluster it on A).
            balance_answer_positions(replaced_questions)

            batch = (await db.execute(
                select(MCQReviewBatch).where(MCQReviewBatch.id == self._batch_id)
            )).scalar_one()
            batch.status = "in_review"
            batch.rejection_feedback = self.feedback
            await db.commit()

        await _job(
            status=JobStatus.completed, progress=100, step="Regeneration complete",
            output={
                "batch_id": str(self._batch_id),
                "rejected": len(rejected),
                "replaced": replaced,
                "skipped": len(skipped),
                **({"skip_reasons": skipped[:20]} if skipped else {}),
            },
        )

    async def _get_skill(self, db: AsyncSession) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(db, "MCQRegenerationAgent")
        except Exception:
            return "Treat the rejection feedback as the brief; fix the exact weakness it names rather than making cosmetic edits."
