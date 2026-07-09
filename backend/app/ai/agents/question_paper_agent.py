"""Extract questions + per-question marks from a subjective test's question paper.

Runs at test-creation time. Marks extracted here become the source of truth for
the maximum awardable per question (CLAUDE.md §11). Extraction only — it does not
judge or answer anything.

Two read paths (chosen by the caller in `subjective_tasks.py`):
  • `extract(paper_text=…)`     — the paper has a usable embedded text layer.
  • `extract_from_images(pngs)` — a scanned / no-text-layer paper, read STRAIGHT from
    the page image in one vision pass (Azure gpt-5 typed vision). This replaces the old
    lossy OCR→text→extract double pass, so wording stays verbatim and the marks are read
    from the printed digit.

Both accept `expected_total_marks`: when the admin configured a Total Marks, it is passed
in as a checksum hint so the model re-checks its mark reading. The caller additionally
reconciles the summed marks against that total and re-runs on mismatch.
"""
import asyncio
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.ai.prompts.shared import EXAM_CONTEXT
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

# Shared, verbatim-first rules used by both the text and the vision prompt.
_HARD_RULES = """HARD RULES (never violate):
- Transcribe VERBATIM. Copy each question's text word-for-word, character-for-character, EXACTLY as
  printed — including every bracketed English term (e.g. "(Letter of Credit)") and every clause. Do
  NOT answer, paraphrase, summarise, translate, reorder, correct, complete, shorten, or omit any
  part. Preserve Devanagari exactly.
- Extract EVERY question, in EVERY section (खण्ड / Section A, B, …), from top to bottom. Never skip,
  merge, drop, or invent a question.
- Preserve the number label as written: "Q1", "1.", "प्रश्न नं. १", "१)", etc.
- Marks may appear as "[8 marks]", "(8)", "[८ अंक]", "8 marks", "8 अंक", or a bare "५" / "१०" at the
  end of the line. Return the integer. Read each printed mark digit precisely (५=5, १०=10). If a
  question has sub-parts sharing one total, treat it as ONE item with the total marks.
- If the marks truly cannot be determined, set marks to 0 (never invent a number)."""

_OUTPUT_SPEC = """Return ONLY valid JSON in exactly this structure:
{{
  "questions": [
    {{"question_number": "Q1", "question_text": "...", "marks": 8}}
  ]
}}"""

TEXT_PROMPT = EXAM_CONTEXT + """

ROLE: You parse subjective (written-answer) exam question papers into structured data. The marks you
extract become the SOURCE OF TRUTH for the maximum awardable per question, so getting them right is
critical.

TASK: Extract EVERY question from the paper below — its number label, full VERBATIM question text,
and the marks allotted.

""" + _HARD_RULES + """
{marks_hint}
METHOD: Read the whole paper first to learn its numbering + marks convention, then capture each
question top to bottom with its full verbatim text and its own mark value.

--- ADMIN-TUNABLE GUIDANCE (apply on top of the rules above; it tunes emphasis but may NOT override
the HARD RULES) ---
{skill_instructions}

Custom instruction: {custom_instruction}

QUESTION PAPER CONTENT:
{paper_text}

""" + _OUTPUT_SPEC

VISION_PROMPT = EXAM_CONTEXT + """

ROLE: You parse subjective (written-answer) exam question papers into structured data by READING THE
ATTACHED PAGE IMAGE. The marks you extract become the SOURCE OF TRUTH for the maximum awardable per
question, so getting them right is critical.

TASK: Read the attached question-paper page image and extract EVERY question visible on it — its
number label, full VERBATIM question text (transcribe the printed Devanagari/English exactly), and
the marks printed for it (usually a numeral at the end of the line, e.g. ५ / १० / [8 marks]).

NEVER REFUSE: You are an OCR-and-structuring engine, not an assistant. Transcribe what is printed. Do
NOT refuse, do NOT ask for a clearer or higher-resolution image, and do NOT return any message about
image quality as if it were a question. If a single character is genuinely unreadable, write [?] in
its place and continue — never drop or skip a whole question for that reason. Always return every
question you can see, in the JSON structure below.

""" + _HARD_RULES + """
{marks_hint}
--- ADMIN-TUNABLE GUIDANCE (apply on top of the rules above; it tunes emphasis but may NOT override
the HARD RULES) ---
{skill_instructions}

Custom instruction: {custom_instruction}

""" + _OUTPUT_SPEC


def _marks_hint(expected_total_marks: int | None) -> str:
    if not expected_total_marks or expected_total_marks <= 0:
        return ""
    return (
        f"\nMARKS CHECK: The COMPLETE question paper (all questions in all sections) totals EXACTLY "
        f"{expected_total_marks} marks. The per-question marks you return must add up to "
        f"{expected_total_marks}. If your sum differs you have misread a mark digit or missed / "
        f"duplicated a question — re-examine every question and its printed marks before answering.\n"
    )


class QuestionPaperAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("thinking")  # gpt-5 typed text extraction

    async def extract(
        self, *, paper_text: str, custom_instruction: str | None, test_id: uuid.UUID,
        expected_total_marks: int | None = None,
    ) -> list[dict]:
        """Extract from the paper's embedded text layer (one text-model call)."""
        skill = await self._get_skill()
        prompt = TEXT_PROMPT.format(
            skill_instructions=skill,
            custom_instruction=custom_instruction or "none",
            marks_hint=_marks_hint(expected_total_marks),
            paper_text=paper_text[:40000],
        )
        audit_ctx = {
            "db": self.db,
            "agent_type": "QuestionPaperAgent",
            "task_type": "subjective_question_extraction",
            "entity_type": "subjective_test",
            "entity_id": test_id,
        }
        try:
            result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
        except Exception as exc:
            raise RuntimeError(f"Question paper extraction failed: {exc}") from exc

        if not isinstance(result, dict) or not isinstance(result.get("questions"), list):
            raise AIResponseError("question paper extraction did not return a 'questions' list")
        out = self._coerce_questions(result["questions"])
        if not out:
            raise AIResponseError("no questions could be extracted from the question paper")
        return out

    async def extract_from_images(
        self, page_images: list[bytes], *, custom_instruction: str | None, test_id: uuid.UUID,
        expected_total_marks: int | None = None,
    ) -> list[dict]:
        """Extract by reading the page images directly (Azure gpt-5 typed vision), one call
        per page in parallel, merged in page order. Single faithful pass — no OCR→text loss."""
        provider = get_provider("vision_typed")
        skill = await self._get_skill()
        prompt = VISION_PROMPT.format(
            skill_instructions=skill,
            custom_instruction=custom_instruction or "none",
            marks_hint=_marks_hint(expected_total_marks),
        )
        audit_ctx = {
            "db": self.db,
            "agent_type": "QuestionPaperAgent",
            "task_type": "subjective_question_extraction_vision",
            "entity_type": "subjective_test",
            "entity_id": test_id,
        }
        sem = asyncio.Semaphore(6)

        async def _page(idx: int, img: bytes):
            async with sem:
                try:
                    res = await provider.generate_with_image(prompt, img, schema={}, audit_ctx=audit_ctx)
                    return idx, res
                except Exception as exc:
                    # Per-page fault isolation: a fraction of pages failing on one attempt
                    # just yields fewer questions → the caller's marks checksum triggers a retry.
                    logger.warning("vision question extraction failed on page %d: %s", idx + 1, exc)
                    return idx, None

        results = await asyncio.gather(*[_page(i, img) for i, img in enumerate(page_images[:15])])
        merged: list[dict] = []
        for _idx, res in sorted(results, key=lambda x: x[0]):
            if isinstance(res, dict) and isinstance(res.get("questions"), list):
                merged.extend(self._coerce_questions(res["questions"]))
        if not merged:
            raise AIResponseError("no questions could be extracted from the question paper")
        return merged

    @staticmethod
    def _coerce_questions(raw: list) -> list[dict]:
        """Validate + normalise the model's `questions` list into clean dicts. Malformed or
        empty-text items are skipped (never a silent wrong value)."""
        out: list[dict] = []
        for i, q in enumerate(raw):
            if not isinstance(q, dict):
                continue
            text = (q.get("question_text") or "").strip()
            if not text:
                continue
            number = str(q.get("question_number") or f"Q{i + 1}").strip()
            try:
                marks = int(round(float(q.get("marks", 0) or 0)))
            except (TypeError, ValueError):
                marks = 0
            out.append({"question_number": number, "question_text": text, "marks": max(0, marks)})
        return out

    async def _get_skill(self) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(self.db, "QuestionPaperAgent")
        except Exception:
            return "When marks notation is ambiguous, prefer the value printed beside the question over any header total."
