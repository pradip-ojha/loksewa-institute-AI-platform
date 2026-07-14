"""Whole-sheet STRUCTURE pass — runs once before page-by-page extraction.

Page-level extraction alone loses context when one answer spans multiple pages or
when students don't write question numbers clearly. This pass shows the vision model
ALL pages at once and produces the page→question MAP: which page holds which question,
where each answer starts/ends, what continues across pages, and where numbering is
unclear.

The pass is split into TWO sequential Gemini calls plus an AI-free merge, because the
map answers two very different sub-questions and the second is where most mistakes
happen:

  • Call 1 — SEGMENTATION + label reading (content-blind): find each answer BLOCK and
    its page span, read the written question label EXACTLY as the ink shows it, judge how
    clear that label is, and describe (neutrally) what the block is about. It does NOT get
    the question texts, so it cannot be tempted to "fix" a label from content — it reports.

  • Call 2 — LABELING decision: assign each block its final question number. A CLEARLY
    written label is trusted as-is and NEVER overridden (a student who genuinely mislabels
    owns that — re-attributing would silently change marks). Only an unclear/missing label
    is resolved from the question TEXT + the block's content.

Splitting gives uniform reliability even when a weak fallback model serves the call, and
a Call-2 failure still degrades to Call-1's clear labels (≈ the old single-call quality)
rather than to nothing. The final output shape is unchanged, so `validate_structure_map`,
`page_hint`, the extraction hint, and `assemble_questionwise` are all untouched.

It is GUIDANCE, not truth — the per-page extractor may correct it from visible evidence.
It does NOT transcribe, check, or grade. Uses the Gemini vision provider.
"""
import json
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.ai.prompts.shared import EXAM_CONTEXT

logger = logging.getLogger(__name__)


# ── Call 1: segmentation + label reading (content-blind) ─────────────────────────
SEGMENT_PROMPT = EXAM_CONTEXT + """

ROLE: You analyze the STRUCTURE of one student's scanned, handwritten Loksewa exam answer sheet.
You are shown ALL pages in order (image 1 = page 1, image 2 = page 2, ...). The handwriting is in
Nepali (Devanagari), English, or a mix — fast, messy, sometimes with cross-outs and corrections.

YOUR JOB — TWO things only: split the sheet into answer BLOCKS, and for each block read the question
number the student WROTE at its start. You do NOT transcribe the answers, do NOT grade, and do NOT
decide which official question a block "really" belongs to (a later step does that from the question
texts). Report only what is visibly on the page.

WHAT A BLOCK IS: one continuous answer — from where the student begins answering a question to where
that answer ends (possibly several pages later). A NEW block begins ONLY at a TOP-LEVEL question
number: a number written to START a new question, typically
  • in or near the LEFT MARGIN, on its own line or followed by the restated question / the answer,
  • often larger, circled, or underlined, written like "1.", "Q1", "1)", "१.", "१)",
    "प्रश्न नं. १", "प्र.सं. १", "प्रश्न १".

DO NOT start a new block for numbering INSIDE an answer. Sub-parts and steps written within one
answer — "क) ख) ग)", "(अ) (आ)", "a) b) c)", "i) ii) iii)", or numbered points/steps "1. 2. 3." used
as list items — stay in the SAME block. When unsure whether a marker is a new question or a sub-part,
use POSITION and LENGTH: a left-margin number followed by a substantial answer = a new question; a
small inline or indented marker inside a running answer = a sub-part (same block).

The test's official question numbers are: {valid_numbers}
Use this list ONLY to recognize the numbering style and sanity-check a reading — never to force an
ambiguous mark onto a number. Report the digits you actually SEE.

For EACH block, in reading order (top-to-bottom, page 1 → last page), report:
- "block_id": 1-based index in reading order (1, 2, 3, ...).
- "pages": every page (1-based) on which this block's writing appears.
- "starts_on_page" / "ends_on_page": first and last page of this block.
- "continues_across_pages": true if the block spans more than one page (its answer runs to the
  bottom of a page and continues at the top of the next with no new question number).
- "written_label": the question number the student WROTE at the block's start, EXACTLY as the ink
  shows it — keep Devanagari as written ("१", "प्रश्न नं. २", "Q3"). If NO number is written at the
  block's start, use "none".
- "label_clarity":
    "clear"   = a number is written and you are confident of its VALUE,
    "unclear" = a number is written but its value is hard to read — smudged, cut off, or it could be
                more than one digit (e.g. "३" vs "४", "1" vs "7", "५" vs "६"),
    "none"    = NO question number is written at the block's start (a continuation page, or the
                student just began writing without a number).
- "content_summary": ONE short, NEUTRAL phrase naming the topic/keywords of the block (e.g.
  "functions of a commercial bank; deposits and credit creation"). Describe only — never judge
  correctness. This is what lets the next step label an unclear block.

HARD RULES:
- Read the written label FAITHFULLY. NEVER "correct" or upgrade it from the content or the known
  list. If it reads like "7" but is smudged, report "7" with clarity "unclear" — do not silently
  turn it into a nearby valid number.
- "unclear" means a number is present but its value is uncertain; "none" means no number is present.
  Do not confuse the two.
- Do NOT invent blocks. A student may leave questions unanswered — report only blocks that contain
  actual writing.
- Judge ONLY from what is visible. This is guidance for a later step, not a final verdict.

Return ONLY valid JSON (no prose, no markdown fences) in exactly this structure:
{{
  "blocks": [
    {{"block_id": 1, "pages": [1, 2], "starts_on_page": 1, "ends_on_page": 2, "continues_across_pages": true, "written_label": "१", "label_clarity": "clear", "content_summary": "..."}}
  ],
  "uncertainty_notes": ""
}}"""


# ── Call 2: labeling decision (trust clear labels, resolve unclear from content) ──
LABEL_PROMPT = EXAM_CONTEXT + """

ROLE: You assign each answer block its final official QUESTION NUMBER. You are shown ALL pages of one
student's handwritten Loksewa answer sheet (image 1 = page 1, ...). Below are the blocks already
found on the sheet (with the number the student wrote and how legible it was) and the official
questions WITH their text.

YOUR JOB: for each block, output the single official question number it answers, following the rules
below EXACTLY.

THE MOST IMPORTANT RULE — trust a clearly written number:
- If a block's "label_clarity" is "clear", the student clearly wrote that number. USE IT as
  "final_number". Do NOT change it because the content looks like a different question — a student
  who writes the wrong number still owns that choice, and re-labeling it would silently change their
  marks. Set "source":"label".
- ONLY exception: if that clearly-written number is NOT in the official list, treat the block as if
  it were "unclear" and infer from content.

When "label_clarity" is "unclear" — a number IS written but its value is uncertain:
- Use the written mark as a STRONG prior and DISAMBIGUATE it with content. Read the block's actual
  writing on its pages (don't rely on the summary alone), then pick the official question whose text
  best matches — preferring a reading consistent with the ambiguous mark.
  Example: the mark could be "३" or "४"; the answer is about the role of the central bank, which is
  question 4 → choose 4. Set "source":"content".

When "label_clarity" is "none" — no number was written:
- Pick the official question whose text best matches the block's writing/content. Set "source":"content".

SIGNAL PRIORITY (highest first): (1) a CLEAR written label — absolute, never overruled; (2) CONTENT
match between the block's writing and a question's text — this is DECISIVE for unclear/none blocks.

DO NOT ASSUME THE ANSWERS ARE IN ORDER. Students very often answer OUT OF SEQUENCE — they attempt the
questions that feel easiest first — so the blocks are frequently NOT in question-number order, and a
question may be skipped or come much later. NEVER label a block from its position alone: e.g. do NOT
assume a block sitting between a clear Q1 and a clear Q3 must be Q2. Physical position is at most a
faint hint when the content is genuinely tied between two questions; the written mark and the content
always decide.

HARD RULES:
- Every "final_number" MUST be one of the official question numbers below.
- It is OK for two blocks to map to the SAME question (a question's start and its continuation).
- Do NOT force every official question to appear, and do NOT invent blocks — label only the blocks
  you are given.

Official questions (number → text):
{questions_block}

Blocks found on the sheet:
{blocks_block}

For EACH block return:
- "block_id": the same id from the block list.
- "final_number": the official question number this block answers.
- "source": "label" if you took a clearly written number, "content" if you inferred it.
- "confidence": 0.0–1.0 — your certainty (a clear label ≈ 1.0; a content inference lower).
- "note": short note ONLY when you inferred a number, or when a written label and the content
  disagree (e.g. "label unclear (३/४), matched Q4 by content"); otherwise "".

Return ONLY valid JSON (no prose, no markdown fences) in exactly this structure:
{{
  "blocks": [
    {{"block_id": 1, "final_number": "1", "source": "label", "confidence": 0.95, "note": ""}}
  ]
}}"""


class AnswerStructureAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("vision")  # Gemini, multi-image

    async def detect(
        self, *, page_pngs: list[bytes], questions: list[dict], sheet_id: uuid.UUID,
    ) -> dict:
        """Two-call structure detection → the page→question `structure_map`.

        `questions` is a list of {"number": str, "text": str}. Best-effort throughout:
        Call 1 failing yields an empty map (extraction runs hint-free, as before); Call 2
        failing degrades to Call-1's clearly-written labels only.
        """
        valid_numbers = [str(q.get("number") or "").strip() for q in questions if q.get("number")]

        # ── Call 1: segmentation + label reading ────────────────────────────────
        seg_blocks, seg_notes = await self._segment(page_pngs, valid_numbers, sheet_id)
        if not seg_blocks:
            # Nothing to label — return the empty map (identical to the old fail path).
            return {"questions": [], "pages": [], "uncertainty_notes": seg_notes}

        # ── Call 2: labeling decision (degrades to clear labels on failure) ──────
        label_map = await self._label(page_pngs, seg_blocks, questions, sheet_id)

        structure_map = _merge_blocks_to_structure_map(seg_blocks, label_map, valid_numbers)
        if seg_notes:
            existing = structure_map.get("uncertainty_notes") or ""
            structure_map["uncertainty_notes"] = " ".join(filter(None, [existing, seg_notes]))
        return structure_map

    async def _segment(
        self, page_pngs: list[bytes], valid_numbers: list[str], sheet_id: uuid.UUID,
    ) -> tuple[list[dict], str]:
        prompt = SEGMENT_PROMPT.format(
            valid_numbers=", ".join(valid_numbers) if valid_numbers else "unknown",
        )
        audit_ctx = {
            "db": self.db,
            "agent_type": "AnswerStructureAgent",
            "task_type": "answer_sheet_structure",
            "entity_type": "student_answer_sheet",
            "entity_id": sheet_id,
        }
        try:
            result = await self.provider.generate_with_images(
                prompt, page_pngs, schema={}, audit_ctx=audit_ctx,
            )
        except Exception as exc:
            # Structure is guidance only — never block checking if it fails.
            logger.warning("structure segmentation failed (continuing without map): %s", exc)
            return [], f"structure pass failed: {exc}"
        if not isinstance(result, dict) or not isinstance(result.get("blocks"), list):
            logger.warning("structure segmentation returned no blocks; continuing without map")
            return [], "structure pass returned no blocks"
        blocks = [b for b in result["blocks"] if isinstance(b, dict)]
        return blocks, (result.get("uncertainty_notes") or "")

    async def _label(
        self, page_pngs: list[bytes], seg_blocks: list[dict],
        questions: list[dict], sheet_id: uuid.UUID,
    ) -> dict[int, dict]:
        """Return {block_id: {final_number, source, confidence, note}}. Empty on failure
        (the merge then degrades to Call-1's clearly-written labels)."""
        questions_block = "\n".join(
            f'- {q.get("number")}: {(q.get("text") or "").strip()}' for q in questions
        ) or "(none)"
        # Only the fields Call 2 needs to reason about each block.
        compact = [{
            "block_id": b.get("block_id"),
            "pages": b.get("pages"),
            "written_label": b.get("written_label"),
            "label_clarity": b.get("label_clarity"),
            "content_summary": b.get("content_summary"),
        } for b in seg_blocks]
        prompt = LABEL_PROMPT.format(
            questions_block=questions_block,
            blocks_block=json.dumps(compact, ensure_ascii=False, indent=2),
        )
        audit_ctx = {
            "db": self.db,
            "agent_type": "AnswerStructureAgent",
            "task_type": "answer_sheet_structure_label",
            "entity_type": "student_answer_sheet",
            "entity_id": sheet_id,
        }
        try:
            result = await self.provider.generate_with_images(
                prompt, page_pngs, schema={}, audit_ctx=audit_ctx,
            )
        except Exception as exc:
            logger.warning("structure labeling failed (degrading to clear labels): %s", exc)
            return {}
        if not isinstance(result, dict) or not isinstance(result.get("blocks"), list):
            logger.warning("structure labeling returned no blocks (degrading to clear labels)")
            return {}
        out: dict[int, dict] = {}
        for b in result["blocks"]:
            if not isinstance(b, dict):
                continue
            try:
                bid = int(b.get("block_id"))
            except (TypeError, ValueError):
                continue
            out[bid] = b
        return out

    @staticmethod
    def page_hint(structure_map: dict, page_number: int) -> str:
        """Build a compact, human-readable hint for one page from the structure map."""
        if not isinstance(structure_map, dict):
            return ""
        parts: list[str] = []
        for p in structure_map.get("pages") or []:
            if isinstance(p, dict) and int(p.get("page") or 0) == page_number:
                qs = ", ".join(str(q) for q in (p.get("question_numbers") or []))
                if qs:
                    parts.append(f"This page is expected to contain answer(s) to question(s): {qs}.")
        for q in structure_map.get("questions") or []:
            if not isinstance(q, dict):
                continue
            pages = [int(x) for x in (q.get("pages") or []) if str(x).isdigit()]
            qn = q.get("question_number")
            if page_number in pages and q.get("continues_across_pages"):
                if int(q.get("starts_on_page") or 0) < page_number:
                    parts.append(f"Question {qn} continues onto this page from a previous page.")
                if int(q.get("ends_on_page") or 0) > page_number:
                    parts.append(f"Question {qn}'s answer continues onto the next page.")
            note = (q.get("note") or "").strip()
            if note and page_number in pages:
                parts.append(f"Q{qn}: {note}")
        un = (structure_map.get("uncertainty_notes") or "").strip()
        if un:
            parts.append(f"Overall note: {un}")
        return " ".join(parts).strip()


def _merge_blocks_to_structure_map(
    seg_blocks: list[dict], label_map: dict[int, dict], valid_numbers: list[str],
) -> dict:
    """Fold Call-1 blocks + Call-2 labels into the legacy `structure_map` shape.

    Each block's final question number comes from Call 2 (`label_map`, keyed by block_id);
    when a block is missing from `label_map` (Call 2 failed/omitted it) we DEGRADE to the
    student's clearly-written label and drop the block otherwise — never guessing here.
    Blocks that resolve to the same question are merged (union pages, widen the span, OR
    the continues flag, concatenate notes). Numbers are normalized to the exact official
    label so grouping doesn't split "१" from "1"; downstream `validate_structure_map` runs
    the same normalization and drops anything unknown.
    """
    # Reuse the canonical script-tolerant matcher (lazy import avoids an import cycle at
    # module load, since the subjective service imports agents).
    from app.modules.subjective.service import _match_question_number

    questions: dict[str, dict] = {}
    notes: list[str] = []

    for b in seg_blocks:
        try:
            bid = int(b.get("block_id"))
        except (TypeError, ValueError):
            bid = None
        decision = label_map.get(bid) if bid is not None else None

        if decision is not None:
            raw_num = str(decision.get("final_number") or "").strip()
            note = (decision.get("note") or "").strip()
        else:
            # Degradation path: trust only a clearly-written label; skip unclear/none.
            if (b.get("label_clarity") or "") != "clear":
                continue
            raw_num = str(b.get("written_label") or "").strip()
            note = ""

        qnum = _match_question_number(raw_num, valid_numbers)
        if not qnum:
            continue

        pages = sorted({int(x) for x in (b.get("pages") or []) if str(x).isdigit()})
        try:
            starts = int(b.get("starts_on_page") or (pages[0] if pages else 0))
        except (TypeError, ValueError):
            starts = pages[0] if pages else 0
        try:
            ends = int(b.get("ends_on_page") or (pages[-1] if pages else 0))
        except (TypeError, ValueError):
            ends = pages[-1] if pages else 0

        entry = questions.get(qnum)
        if entry is None:
            questions[qnum] = {
                "question_number": qnum,
                "pages": set(pages),
                "starts_on_page": starts or (pages[0] if pages else 0),
                "ends_on_page": ends or (pages[-1] if pages else 0),
                "continues_across_pages": bool(b.get("continues_across_pages")),
                "note": note,
            }
        else:
            entry["pages"].update(pages)
            if starts:
                entry["starts_on_page"] = min(entry["starts_on_page"] or starts, starts)
            entry["ends_on_page"] = max(entry["ends_on_page"] or ends, ends)
            entry["continues_across_pages"] = (
                entry["continues_across_pages"] or bool(b.get("continues_across_pages"))
            )
            if note:
                entry["note"] = " ".join(filter(None, [entry.get("note"), note]))
        if note:
            notes.append(f"Q{qnum}: {note}")

    # Finalize questions[] (spanning >1 page also implies continues).
    out_questions: list[dict] = []
    page_to_qs: dict[int, list[str]] = {}
    for qnum, e in questions.items():
        pages = sorted(e["pages"])
        continues = bool(e["continues_across_pages"]) or len(pages) > 1
        out_questions.append({
            "question_number": qnum,
            "pages": pages,
            "starts_on_page": e["starts_on_page"] or (pages[0] if pages else 0),
            "ends_on_page": e["ends_on_page"] or (pages[-1] if pages else 0),
            "continues_across_pages": continues,
            "note": e.get("note") or "",
        })
        for p in pages:
            page_to_qs.setdefault(p, [])
            if qnum not in page_to_qs[p]:
                page_to_qs[p].append(qnum)

    out_pages = [{"page": p, "question_numbers": page_to_qs[p]} for p in sorted(page_to_qs)]
    return {
        "questions": out_questions,
        "pages": out_pages,
        "uncertainty_notes": " ".join(notes).strip(),
    }
