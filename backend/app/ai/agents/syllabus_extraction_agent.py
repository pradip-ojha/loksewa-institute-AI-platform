"""Extract a full chapter → topic → subtopic syllabus tree from an uploaded PDF/Word file.

Runs as a background job right after an admin creates an exam (or from the Syllabus page's
"Import from PDF" action). Instead of the admin typing every leaf by hand, the file is OCR'd /
read and structured into the exact tree shape the seeds use, then written straight into
`syllabus_items` for the exam. The admin can add/edit/delete anything afterward on the Syllabus
page.

Reuses the Knowledge Layer's OCR/render helpers (`knowledge_processing_agent`) but with a
DIFFERENT vision prompt: the knowledge OCR prompt deliberately *rejects* syllabus/course-outline
pages as non-content — here that content is exactly what we want to keep.

Structural extraction only (like `KnowledgeProcessingAgent`) — it is intentionally NOT
skill-tunable (no admin skill dial), so it never calls `get_active_skill_text`.
"""
import asyncio
import logging
import uuid

from sqlalchemy import select

from app.ai.agents.knowledge_processing_agent import (
    _classify_page_text,
    _docx_to_pdf_bytes,
    _extract_pdf_pages_text,
    _extract_text_from_docx,
    _render_all_regions,
)
from app.ai.model_router import get_provider
from app.ai.prompts.shared import EXAM_CONTEXT
from app.core.database import AsyncSessionLocal
from app.core.exceptions import AIResponseError
from app.integrations.r2_client import get_r2
from app.modules.exams.models import Exam
from app.modules.files.models import File
from app.modules.jobs.models import JobStatus
from app.modules.jobs.service import update_job

logger = logging.getLogger(__name__)

# Per-page OCR prompt for syllabus documents. Unlike the knowledge OCR prompt this NEVER rejects
# a page — a syllabus/course-outline page IS the content we want. Transcribe verbatim so the
# structuring pass sees the real chapter/topic/subtopic wording.
SYLLABUS_OCR_PROMPT = """You are a text-transcription (OCR) engine reading one page (or one column of a page) of a Loksewa / banking exam SYLLABUS or course outline (Nepali, English, or mixed). Transcribe EVERYTHING EXACTLY as printed — this is a list of exam chapters, topics and sub-topics and every line matters.

- Transcribe the text character for character, in natural reading order (top to bottom). Do NOT interpret, rephrase, translate, summarise, reorder, correct, add or drop anything, and never use your own knowledge — transcribe only what you can see.
- Nepali → Unicode Devanagari exactly as written (never Romanised or legacy ASCII). English → exactly as shown. Mixed → preserve both scripts. Keep all numbering exactly (1. 2. / १. २. / (a) (b) / •, -).
- Preserve the outline structure: headings on their own line, and every numbered/bulleted item kept under its parent (reflect indentation by keeping sub-items after their parent). Tables row by row, columns separated by |.
- If a word is genuinely unreadable, write [?] in its place — never guess it from meaning.
- Leave out ONLY running headers/footers, page numbers and watermarks.

Output only the transcribed text, nothing else."""

SYLLABUS_STRUCTURE_PROMPT = EXAM_CONTEXT + """

ROLE: You convert a raw exam SYLLABUS / course outline (already transcribed from a PDF or Word
file) into a clean, structured chapter → topic → subtopic tree that will be stored as the exam's
official syllabus.

TASK: Read the syllabus text below and organise it into chapters, the topics within each chapter,
and the subtopics within each topic.

HARD RULES (never violate):
- Use the document's OWN wording for chapter/topic/subtopic names — transcribe them, do not
  paraphrase, translate, or "improve" them. Preserve Devanagari and bilingual names verbatim.
- Do NOT invent chapters, topics, or subtopics that are not present in the text. If the document
  only lists chapters and topics with no finer breakdown, return empty "subtopics" arrays — never
  fabricate subtopics to fill them.
- Map the outline's real hierarchy: a numbered/lettered item nested under a heading is a subtopic
  of that heading's topic; a top-level section is a chapter. If the document is only two levels
  deep (chapters → topics), put the items as topics with empty subtopics.
- IGNORE non-syllabus material: cover/title text, the exam's name/level, marks scheme / full-marks
  / pass-marks / time-allowed tables, exam pattern/scheme prose, general instructions, and page
  decoration. Keep only the actual chapter/topic/subtopic content.
- Every chapter MUST have at least one topic (a chapter heading with items directly under it →
  those items are its topics). Drop a chapter that ends up with no topics.

METHOD: First skim to learn the outline's numbering/heading convention, then walk it top to bottom
capturing each chapter, its topics, and each topic's subtopics using the exact source wording.

SYLLABUS TEXT:
{syllabus_text}

Return ONLY valid JSON in exactly this structure:
{{
  "chapters": [
    {{
      "chapter": "chapter name exactly as written",
      "topics": [
        {{"topic": "topic name exactly as written", "subtopics": ["subtopic 1", "subtopic 2"]}}
      ]
    }}
  ]
}}"""


class SyllabusExtractionAgent:
    """OCR/read a syllabus file and structure it into a chapter/topic/subtopic tree."""

    async def process(self, *, job_id: str, exam_id: str, file_id: str) -> None:
        job_uuid = uuid.UUID(job_id)
        exam_uuid = uuid.UUID(exam_id)
        file_uuid = uuid.UUID(file_id)

        async def _step(progress: int, step: str) -> None:
            # Cosmetic progress update on its own short session — never fail the job on it.
            try:
                async with AsyncSessionLocal() as step_db:
                    await update_job(step_db, job_uuid, status=JobStatus.processing,
                                     progress=progress, step=step)
            except Exception as exc:
                logger.warning("Could not update syllabus job progress (%d%%): %s", progress, exc)

        await _step(5, "Loading exam and file record…")

        # ── PHASE 1: LOAD (short session, then close) ──────────────────────────────
        # Raise (not update+return) on missing rows so run_task records `failed` — a
        # normal return from work() makes run_task stamp the job `completed`.
        async with AsyncSessionLocal() as load_db:
            exam = (await load_db.execute(
                select(Exam).where(Exam.id == exam_uuid)
            )).scalar_one_or_none()
            if not exam:
                raise RuntimeError("Exam not found.")

            file_record = (await load_db.execute(
                select(File).where(File.id == file_uuid)
            )).scalar_one_or_none()
            if not file_record:
                raise RuntimeError("Uploaded file record not found.")
            r2_key = file_record.r2_key
            mime = file_record.mime_type

        # ── PHASE 2: WORK (no DB session held) ─────────────────────────────────────
        await _step(15, "Downloading file from storage…")
        file_bytes = await asyncio.to_thread(get_r2().download_fileobj, r2_key)

        await _step(25, "Reading syllabus text…")
        syllabus_text = await self._extract_text(file_bytes, mime, _step)
        if not syllabus_text.strip():
            raise RuntimeError(
                "No text could be read from the syllabus file. Check that the PDF/Word file is "
                "readable and not password-protected, and that it contains the syllabus outline."
            )

        await _step(65, "Structuring chapters, topics and subtopics…")
        tree = await self._structure(syllabus_text, exam_uuid)
        if not tree:
            raise AIResponseError(
                "The file was read but no chapters/topics could be identified in it. Make sure the "
                "uploaded file is the exam syllabus / course outline."
            )

        # ── PHASE 3: SAVE (fresh session, idempotent) ──────────────────────────────
        await _step(85, "Saving syllabus…")
        from app.modules.syllabus.service import replace_syllabus_tree
        async with AsyncSessionLocal() as save_db:
            counts = await replace_syllabus_tree(save_db, exam_uuid, tree)

        async with AsyncSessionLocal() as done_db:
            await update_job(
                done_db, job_uuid, status=JobStatus.completed, progress=100,
                step=(f"Imported {counts['chapters']} chapters, {counts['topics']} topics, "
                      f"{counts['subtopics']} subtopics."),
                output=counts,
            )
        logger.info("Syllabus imported for exam %s: %s", exam_id, counts)

    # ── text acquisition ──────────────────────────────────────────────────────────

    async def _extract_text(self, file_bytes: bytes, mime: str, step_cb) -> str:
        if "pdf" in mime:
            return await self._ocr_or_textlayer_pdf(file_bytes, step_cb)
        if "word" in mime or "docx" in mime or "msword" in mime:
            raw = await asyncio.to_thread(_extract_text_from_docx, file_bytes)
            if _classify_page_text(raw) == "valid_unicode":
                logger.info("Syllabus DOCX is valid Unicode → using extracted text directly")
                return raw
            logger.info("Syllabus DOCX not valid Unicode → converting to PDF for OCR")
            await step_cb(30, "Converting Word document to PDF for OCR…")
            pdf_bytes = await asyncio.to_thread(_docx_to_pdf_bytes, file_bytes)
            return await self._ocr_or_textlayer_pdf(pdf_bytes, step_cb)
        raise RuntimeError(f"Unsupported file type for syllabus extraction: {mime}")

    async def _ocr_or_textlayer_pdf(self, file_bytes: bytes, step_cb) -> str:
        """Use the PDF text layer when it's clean Unicode; otherwise OCR every page."""
        page_texts = await asyncio.to_thread(_extract_pdf_pages_text, file_bytes)
        joined = "\n\n".join(page_texts)
        if _classify_page_text(joined) == "valid_unicode":
            logger.info("Syllabus PDF text layer is valid Unicode → using it directly")
            return joined

        total = len(page_texts)
        await step_cb(35, f"Vision OCR — reading {total} page(s)…")
        provider = get_provider("vision_typed")
        # High-fidelity 300-DPI PNG, auto column-split (same helper as the Knowledge Layer):
        # dense two-column syllabus pages are read one column at a time in reading order.
        units = await asyncio.to_thread(_render_all_regions, file_bytes, list(range(total)), 300)

        sem = asyncio.Semaphore(6)
        ATTEMPTS = 3

        async def _ocr_region(page_i: int, region_i: int, png: bytes) -> tuple[int, int, str]:
            tag = f"page {page_i + 1} region {region_i + 1}"
            async with sem:
                for attempt in range(1, ATTEMPTS + 1):
                    try:
                        result = await provider.generate_with_image(
                            SYLLABUS_OCR_PROMPT, png, schema=None
                        )
                        text = (result.get("text") or "").strip()
                        if text:
                            return (page_i, region_i, text)
                    except Exception as exc:
                        if "404" in str(exc):
                            logger.error("Vision not supported by this deployment (404) — %s", tag)
                            return (page_i, region_i, "")
                        logger.warning("Syllabus OCR failed %s (attempt %d/%d): %s",
                                       tag, attempt, ATTEMPTS, exc)
                logger.error("Syllabus OCR exhausted attempts on %s — region skipped", tag)
                return (page_i, region_i, "")

        results = await asyncio.gather(*[_ocr_region(p, r, png) for p, r, png in units])
        results.sort(key=lambda x: (x[0], x[1]))
        return "\n\n".join(t for _, _, t in results if t)

    # ── structuring ───────────────────────────────────────────────────────────────

    async def _structure(self, syllabus_text: str, exam_id: uuid.UUID) -> list[dict]:
        provider = get_provider("thinking")
        prompt = SYLLABUS_STRUCTURE_PROMPT.format(syllabus_text=syllabus_text[:40000])
        # A fresh short session purely so the AI-audit row can be written independently.
        async with AsyncSessionLocal() as audit_db:
            audit_ctx = {
                "db": audit_db,
                "agent_type": "SyllabusExtractionAgent",
                "task_type": "syllabus_extraction",
                "entity_type": "exam",
                "entity_id": exam_id,
            }
            try:
                result = await provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
            except Exception as exc:
                raise RuntimeError(f"Syllabus structuring failed: {exc}") from exc

        if not isinstance(result, dict) or not isinstance(result.get("chapters"), list):
            raise AIResponseError("syllabus extraction did not return a 'chapters' list")

        return self._clean_tree(result["chapters"])

    @staticmethod
    def _clean_tree(chapters: list) -> list[dict]:
        """Coerce/validate the AI tree into a clean, deduped list; drop empty nodes."""
        out: list[dict] = []
        seen_chapters: set[str] = set()
        for ch in chapters:
            if not isinstance(ch, dict):
                continue
            chapter = (ch.get("chapter") or "").strip()
            if not chapter or chapter.lower() in seen_chapters:
                continue
            topics_out: list[dict] = []
            seen_topics: set[str] = set()
            for tp in ch.get("topics") or []:
                if not isinstance(tp, dict):
                    continue
                topic = (tp.get("topic") or "").strip()
                if not topic or topic.lower() in seen_topics:
                    continue
                subs: list[str] = []
                seen_subs: set[str] = set()
                for s in tp.get("subtopics") or []:
                    if not isinstance(s, str):
                        continue
                    s = s.strip()
                    if s and s.lower() not in seen_subs:
                        subs.append(s)
                        seen_subs.add(s.lower())
                topics_out.append({"topic": topic, "subtopics": subs})
                seen_topics.add(topic.lower())
            if not topics_out:
                continue  # a chapter with no topics is not storable
            out.append({"chapter": chapter, "topics": topics_out})
            seen_chapters.add(chapter.lower())
        return out
