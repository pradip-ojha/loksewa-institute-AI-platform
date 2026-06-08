"""Whole-sheet STRUCTURE pass — runs once before page-by-page extraction.

Page-level extraction alone loses context when one answer spans multiple pages or
when students don't write question numbers clearly. This pass shows the vision model
ALL pages at once plus the admin question list, and asks only for the page→question
MAP: which page holds which question, where each answer starts/ends, what continues
across pages, and where numbering is unclear.

It is GUIDANCE, not truth — the per-page extractor may correct it from visible
evidence. It does NOT transcribe, check, or grade. Uses the Gemini vision provider.
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

STRUCTURE_PROMPT = """You are analysing the STRUCTURE of a scanned exam answer sheet (Nepali, English, or mixed). You are shown ALL pages of one student's answer sheet, in order (image 1 = page 1, image 2 = page 2, ...).

Do NOT transcribe, check, or grade anything. Only map which question is answered where.

The test has these question numbers: {valid_numbers}

For EACH question that appears anywhere in the sheet, report:
- "question_number": match it to one of the known labels above when visible; if an answer continues with no new label, attribute it to the question it continues.
- "pages": the list of page numbers (1-based) that contain any part of this question's answer.
- "starts_on_page" / "ends_on_page": first and last page of this answer.
- "continues_across_pages": true if the answer spans more than one page.
- "note": short note ONLY if the question number is unclear/ambiguous on the page (else "").

Also report, per page, which question numbers appear on it, and any overall uncertainty.

Return ONLY valid JSON in exactly this structure:
{{
  "questions": [
    {{"question_number": "1", "pages": [1, 2], "starts_on_page": 1, "ends_on_page": 2, "continues_across_pages": true, "note": ""}}
  ],
  "pages": [
    {{"page": 1, "question_numbers": ["1", "2"]}}
  ],
  "uncertainty_notes": ""
}}"""


class AnswerStructureAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("vision")  # Gemini, multi-image

    async def detect(
        self, *, page_pngs: list[bytes], valid_numbers: list[str], sheet_id: uuid.UUID,
    ) -> dict:
        prompt = STRUCTURE_PROMPT.format(
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
            logger.warning("structure pass failed (continuing without map): %s", exc)
            return {"questions": [], "pages": [], "uncertainty_notes": f"structure pass failed: {exc}"}

        if not isinstance(result, dict):
            raise AIResponseError("structure pass did not return an object")
        result.setdefault("questions", [])
        result.setdefault("pages", [])
        result.setdefault("uncertainty_notes", "")
        return result

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
