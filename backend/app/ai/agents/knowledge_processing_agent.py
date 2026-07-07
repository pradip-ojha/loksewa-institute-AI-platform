import asyncio
import json
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from io import BytesIO
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.ai.prompts.shared import EXAM_CONTEXT
from app.core.database import AsyncSessionLocal
from app.integrations.pinecone_client import get_pinecone
from app.integrations.r2_client import get_r2
from app.modules.files.models import File
from app.modules.jobs.service import update_job
from app.modules.jobs.models import JobStatus
from app.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument
from app.modules.exams.models import Exam

logger = logging.getLogger(__name__)

# ── TEMPORARY DEBUG INSTRUMENTATION ────────────────────────────────────────
# Dumps each stage of knowledge ingestion (raw OCR text, section boundaries,
# raw AI extraction JSON, final chunks) to files so a bad model_qa run can be
# inspected step-by-step. Enable by setting KNOWLEDGE_DEBUG_DUMP=1 in backend/.env
# (read via Settings, since pydantic-settings loads .env into the config object,
# NOT into os.environ); files land under backend/debug_dumps/<document_id>/.
# REMOVE THIS BLOCK (and its call sites) once the model_qa issue is diagnosed.
_DEBUG_DIR = Path(__file__).resolve().parents[3] / "debug_dumps"


def _debug_dump(document_id: str, filename: str, content: str) -> None:
    """Best-effort write of a pipeline-stage artifact for debugging. Never raises."""
    from app.core.config import get_settings

    if not get_settings().KNOWLEDGE_DEBUG_DUMP:
        return
    try:
        out_dir = _DEBUG_DIR / str(document_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / filename).write_text(content, encoding="utf-8")
        logger.info("[DEBUG DUMP] wrote %s", out_dir / filename)
    except Exception as exc:  # pragma: no cover - debug only
        logger.warning("[DEBUG DUMP] failed to write %s: %s", filename, exc)

# Connection-level errors that mean "the DB connection died" rather than "the SQL
# was wrong". On a flaky network a pooled asyncpg connection can be dropped by the
# server (Neon) *mid-query* — pool_pre_ping only validates on checkout, so it
# cannot prevent this. These are safe to retry on a fresh connection.
_DB_DISCONNECT_ERRORS = (DBAPIError, OperationalError, InterfaceError)


def _is_db_disconnect(exc: Exception) -> bool:
    """True if `exc` is a dropped/stale DB connection (retryable), not a real
    SQL/constraint error (which must surface)."""
    if isinstance(exc, _DB_DISCONNECT_ERRORS):
        # SQLAlchemy flags invalidated connections; also treat asyncpg's
        # connection-does-not-exist / connection-closed as disconnects by name.
        if getattr(exc, "connection_invalidated", False):
            return True
        text = f"{type(getattr(exc, 'orig', exc)).__name__}: {exc}".lower()
        return any(
            marker in text
            for marker in ("connectiondoesnotexist", "connection was closed",
                           "connection is closed", "connection reset",
                           "server closed the connection", "interfaceerror")
        )
    return False


async def _db_op_with_retry(op, *, attempts: int = 4, label: str = "db op"):
    """Run an async DB operation, retrying transient connection drops.

    `op` is a zero-arg coroutine factory that opens its OWN fresh session and
    performs an idempotent unit of work. Each retry therefore checks out a fresh
    connection (pool_pre_ping validates it), which is the only way to recover
    from a connection the server killed mid-operation.
    """
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await op()
        except Exception as exc:  # noqa: BLE001 — re-raised below if not retryable
            if not _is_db_disconnect(exc):
                raise
            last_exc = exc
            if attempt < attempts:
                delay = min(2.0 * attempt, 8.0)
                logger.warning(
                    "%s hit a transient DB disconnect (attempt %d/%d): %s — retrying in %.0fs",
                    label, attempt, attempts, exc, delay,
                )
                await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc

# NOTE: The old LLM "Preeti decode" fallback was removed deliberately — decoding garbled
# ASCII without seeing the glyphs is unreliable and would ingest meaningless chunks into
# the vector store. The pipeline is now OCR-only: Preeti/scanned content is read from the
# rendered image (PDFs directly; DOCX after a LibreOffice DOCX→PDF conversion).

VISION_EXTRACT_PROMPT = """You are a text-transcription (OCR) engine. Transcribe the text in this image EXACTLY as printed.

- Output the exact text, character for character, in natural reading order (top to bottom).
- Do NOT interpret, explain, summarise, rephrase, translate, reorder, correct, or complete anything. Do NOT add any word that is not printed and do NOT drop any word that is printed. Use only what you can see — never your own knowledge.
- Nepali → Unicode Devanagari exactly as written (never Romanised). English → exactly as written. Keep all numbers, symbols and English terms exactly as shown (e.g. १/२/३ or 1/2/3 as printed).
- Preserve the printed line breaks, paragraphs, headings, numbering and bullets as they appear.
- If a character or word is genuinely unreadable, write [?] in its place — never guess it from meaning.
- The ONLY thing you may leave out: running page headers/footers, page numbers and watermarks. Transcribe everything else verbatim.

Output only the transcribed text, nothing else."""

CHUNK_PROMPT = EXAM_CONTEXT + """

ROLE: You prepare Nepali Loksewa/banking study material for semantic retrieval. Downstream agents
(MCQ generation, answer checking, the video tutor) will fetch these chunks by meaning, so each
chunk must stand on its own and carry one coherent idea.

TASK: Split the text below into meaningful, self-contained chunks and map each one to the OFFICIAL
exam syllabus.

HARD RULES (never violate):
- Each chunk is complete and independently understandable out of context. Preserve Devanagari and
  technical/Loksewa terms exactly; never translate, summarise, or add content.
- GOOD chunks: a complete definition + its explanation, one whole concept block, a full worked
  example, or a coherent group of exam points.
- BAD chunks: mid-sentence cuts, context-free fragments, or giant multi-topic blocks.
- Target 100–500 words per chunk and NEVER cut a sentence mid-way.

Context:
- Document type: {document_type}
- Exam type: {exam_type}
- Custom instruction: {custom_instruction}

OFFICIAL SYLLABUS TREE (chapter → topic → subtopic) — the ONLY valid labels. The source material may
use different chapter/topic names or a different ordering than the official syllabus; use its own
headings/numbering as EVIDENCE together with the content, but always OUTPUT exact official strings from
the tree below — never the source's own names as the output label:
{syllabus_tree}

{mapping_block}

Return a JSON object with these keys, in this order:
  "section_analysis" : {{"form": "detailed" | "mixed", "chapters_involved": [exact CHAPTER strings from the tree]}}
  "chunks"           : an array where each item has:
    "content"      : the chunk text (string)
    "content_type" : one of [concept_explanation, definition, example, exam_point, procedure, comparison, list_items, summary]
    "chapter"      : exact CHAPTER string from the tree above, or null
    "topic"        : exact TOPIC string from the tree above, or null
    "subtopic"     : exact SUBTOPIC string from the tree above, or null
    "language"     : "english" | "nepali" | "nepali_english_mixed"

Example format:
{{"section_analysis": {{"form": "detailed", "chapters_involved": ["..."]}}, "chunks": [{{"content": "...", "content_type": "definition", "chapter": "...", "topic": "...", "subtopic": null, "language": "nepali"}}]}}

TEXT TO CHUNK:
{text}
"""

# The mapping-rules block injected into CHUNK_PROMPT differs by ingest mode (CLAUDE.md §8):
# whole-book (no admin chapter) → the model analyses the whole section first (form + chapters
# involved), then maps each chunk top-down (detailed) or bounded-independent (mixed);
# single-chapter (admin picked a chapter) → the chapter is fixed and only topic/subtopic vary.
MAPPING_BLOCK_WHOLE_BOOK = """MAPPING RULES — analyse the whole section FIRST, then map each chunk:

STEP 1 — SECTION ANALYSIS (decide this before any chunk, and output it first):
- "chapters_involved": the OFFICIAL chapter(s) this section covers — usually ONE, at most a few. Use BOTH
  the section's own headings/numbering AND its content as evidence, but list only exact CHAPTER strings
  from the tree above.
- "form":
    "detailed" = it explains a few subjects in depth (long paragraphs/answers) — one section cannot hold a
                 whole chapter, so it is 1–2 chapters.
    "mixed"    = it is a list of many short items spanning different subjects.

STEP 2 — PER-CHUNK MAPPING:
- If form is "detailed": give EVERY chunk one of the chapters from chapters_involved (default to the main
  one; switch only where a chunk clearly starts another identified chapter). Then pick a TOPIC (and its
  SUBTOPIC) within that chapter. If no topic fits, KEEP the chapter and set topic/subtopic null.
- If form is "mixed": classify each chunk on its own, but ONLY among the chapters in chapters_involved;
  then pick TOPIC/SUBTOPIC within it. If a chunk fits none of them, set chapter/topic/subtopic null.
- Always copy exact strings from the tree; never invent labels."""

MAPPING_BLOCK_LOCKED = """MAPPING RULES (this whole document belongs to CHAPTER: {chapter}):
- SECTION ANALYSIS (output it first): set "chapters_involved" to exactly ["{chapter}"] and "form" to
  "detailed" or "mixed" as best describes the section.
- The chapter is FIXED — set every chunk's "chapter" to exactly "{chapter}".
- Choose only the TOPIC (and its SUBTOPIC) within that chapter, using exact strings from the tree.
- If a chunk matches no listed topic, set topic and subtopic to null (chapter stays "{chapter}").
- Never invent labels that are not in the tree above."""


# Extraction prompt for `model_qa` documents (question papers with model answers). Unlike the prose
# chunker, the unit of meaning here is a QUESTION ↔ ANSWER pair: each pair becomes one chunk whose
# "content" is the ANSWER and which additionally carries the "question". Retrieval embeds
# question+answer together and can match on the question, so the pairing must be preserved exactly.
QA_EXTRACT_PROMPT = EXAM_CONTEXT + """

ROLE: You prepare Nepali Loksewa/banking MODEL ANSWER material for semantic retrieval. The document
below is a question paper together with its model/ideal answers. Downstream agents fetch these by
meaning to ground answer-checking and tutoring.

TASK: Extract EVERY complete question–answer pair and map each one to the OFFICIAL exam syllabus.

HARD RULES (never violate):
- One item per question. "answer" = the FULL model answer to that question, transcribed VERBATIM
  (preserve Devanagari, formulas, tables, numbering and technical/Loksewa terms exactly). Never
  reword, translate, summarise, shorten, or add content.
- "question" = the question text exactly as written (drop only the printed marks/number decoration
  like "[8 marks]" / "प्रश्न नं. १" if you wish, but keep the actual question wording).
- If a question's answer is CUT OFF at the end of this excerpt (incomplete), SKIP that pair — it
  reappears complete in the next overlapping excerpt. Never emit a half answer.
- Ignore pure front-matter (cover, instructions) that is not a question with an answer.

Context:
- Exam type: {exam_type}
- Custom instruction: {custom_instruction}

OFFICIAL SYLLABUS TREE (chapter → topic → subtopic) — the ONLY valid labels. The paper may use
different chapter/topic names or ordering than the official syllabus; use its own headings/numbering as
EVIDENCE together with the content, but always OUTPUT exact official strings from the tree below —
never the paper's own names as the output label:
{syllabus_tree}

{mapping_block}

Return a JSON object with these keys, in this order:
  "section_analysis" : {{"form": "detailed" | "mixed", "chapters_involved": [exact CHAPTER strings from the tree]}}
  "pairs"            : an array where each item has:
    "question"  : the question text (string)
    "answer"    : the full model answer text (string)
    "chapter"   : exact CHAPTER string from the tree above, or null
    "topic"     : exact TOPIC string from the tree above, or null
    "subtopic"  : exact SUBTOPIC string from the tree above, or null
    "language"  : "english" | "nepali" | "nepali_english_mixed"

Example format:
{{"section_analysis": {{"form": "detailed", "chapters_involved": ["..."]}}, "pairs": [{{"question": "...", "answer": "...", "chapter": "...", "topic": "...", "subtopic": null, "language": "nepali"}}]}}

TEXT TO EXTRACT PAIRS FROM:
{text}
"""

# ── PDF text classification ────────────────────────────────────────────────────

_DEVANAGARI_START = "ऀ"
_DEVANAGARI_END   = "ॿ"


def _has_devanagari(text: str) -> bool:
    return any(_DEVANAGARI_START <= ch <= _DEVANAGARI_END for ch in text)


def _classify_page_text(text: str) -> str:
    """
    Classify the quality of text extracted by PyMuPDF from one PDF page.

    Returns one of:
      'valid_unicode'  — has real Devanagari Unicode; use as-is
      'legacy_font'    — ASCII characters from Preeti/Kantipur encoding; needs vision
      'empty'          — too little text (scanned/image page or blank); needs vision
      'broken'         — high proportion of replacement chars or non-printable; needs vision
    """
    stripped = text.strip()

    # Not enough text to be useful → scanned or blank page
    if len(stripped) < 20:
        return "empty"

    # Real Unicode Devanagari present → valid
    if _has_devanagari(stripped):
        return "valid_unicode"

    # Replacement characters dominate → corrupted extraction
    replacement = stripped.count("�")
    if replacement / len(stripped) > 0.04:
        return "broken"

    # Substantial text but no Devanagari → check ASCII ratio
    # Legacy Nepali fonts (Preeti, Kantipur) map Devanagari to ASCII printable chars.
    # These pages have very high ASCII printable ratios with no Unicode Devanagari.
    ascii_printable = sum(1 for ch in stripped if 32 <= ord(ch) <= 126)
    if ascii_printable / len(stripped) > 0.65:
        return "legacy_font"

    return "broken"


# Force vision OCR on EVERY PDF page regardless of the text-layer classification.
# Rationale: the source documents are all either scanned or written in legacy Nepali
# fonts (Preeti/Kantipur), whose PDF text layer is unusable ASCII garbage even when the
# page "parses". Re-OCR'ing every page is the safe default for this corpus. The
# per-page classification below is still computed — it drives the fallback chain
# (Preeti-decode / raw text) when a vision call fails. Flip this to False to restore
# the cost-saving behavior that trusts clean Unicode text layers.
FORCE_OCR_ALL_PAGES = True


def _needs_vision(classification: str) -> bool:
    if FORCE_OCR_ALL_PAGES:
        return True
    return classification != "valid_unicode"


# ── PDF helpers ────────────────────────────────────────────────────────────────

def _extract_pdf_pages_text(data: bytes) -> list[str]:
    """Extract raw text from each PDF page. Returns one string per page."""
    import fitz
    doc = fitz.open(stream=data, filetype="pdf")
    texts = [page.get_text() for page in doc]
    doc.close()
    return texts


# ── Column-aware page rendering (OCR fidelity) ──────────────────────────────────
# Vision OCR reads a full page at a fixed resolution budget (~768px on the short side),
# so a dense two-column Nepali page leaves too few pixels per glyph and the model starts
# guessing. We therefore split each page at its central whitespace gutter and OCR each
# column separately (≈2× pixels/glyph, and correct left→right reading order). Single-column
# pages have no gutter → rendered whole (never split mid-line).

# Fraction of page width searched for the gutter (a two-column layout gutters near centre).
_GUTTER_BAND = (0.40, 0.60)
# A column x is a gutter only if it is ink-free over at least this fraction of the page
# height (below 1.0 so a full-width running header/footer band does not disqualify it)…
_GUTTER_MIN_CLEAR_ROWS = 0.85
# …and each side must carry at least this fraction of the page's total ink (real content
# on BOTH sides — guards against splitting an off-centre single column).
_GUTTER_MIN_SIDE_INK = 0.15


def _detect_column_gutter(page, analysis_dpi: int = 120) -> float | None:
    """Return the x of a clean vertical column gutter as a fraction of page width, or None.

    Renders the page to a cheap grayscale raster, thresholds it to an ink mask, and looks in
    the central band for a column that is ink-free down (almost) the whole height with real
    content on both sides. Deterministic; works for vector and scanned pages alike.
    """
    import fitz
    import cv2
    import numpy as np

    try:
        mat = fitz.Matrix(analysis_dpi / 72, analysis_dpi / 72)
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
        # Ink = dark pixels. Otsu picks the page-specific text/background split.
        _, ink = cv2.threshold(img, 0, 1, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
        h, w = ink.shape
        if w < 40 or h < 40:
            return None

        total_ink = int(ink.sum())
        if total_ink <= 0:
            return None

        col_ink = ink.sum(axis=0)                       # ink pixels per column x
        clear_rows_frac = 1.0 - (col_ink / float(h))    # fraction of rows empty at each x

        x0 = int(w * _GUTTER_BAND[0])
        x1 = int(w * _GUTTER_BAND[1])
        if x1 <= x0:
            return None
        band = clear_rows_frac[x0:x1]
        best_local = int(band.argmax())
        best_x = x0 + best_local
        if clear_rows_frac[best_x] < _GUTTER_MIN_CLEAR_ROWS:
            return None

        left_ink = int(ink[:, :best_x].sum())
        right_ink = int(ink[:, best_x:].sum())
        if left_ink < total_ink * _GUTTER_MIN_SIDE_INK:
            return None
        if right_ink < total_ink * _GUTTER_MIN_SIDE_INK:
            return None

        return best_x / float(w)
    except Exception as exc:  # never let detection break ingestion — fall back to whole page
        logger.warning("column-gutter detection failed (using whole page): %s", exc)
        return None


def _render_page_regions(data: bytes, page_index: int, dpi: int = 300) -> list[bytes]:
    """Render one PDF page to high-fidelity PNG region(s) in reading order.

    Two-column page → [left_png, right_png] split exactly on the empty gutter (no glyph cut,
    no overlap, no duplication). Single-column page → [whole_page_png]. PNG is lossless so
    thin Devanagari strokes are not smeared by JPEG compression.
    """
    import fitz
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        page = doc[page_index]
        rect = page.rect
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        gutter = _detect_column_gutter(page)
        if gutter is None:
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
            return [pix.tobytes("png")]
        gx = rect.width * gutter
        clips = [
            fitz.Rect(rect.x0, rect.y0, rect.x0 + gx, rect.y1),   # left column
            fitz.Rect(rect.x0 + gx, rect.y0, rect.x1, rect.y1),   # right column
        ]
        out: list[bytes] = []
        for clip in clips:
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB, clip=clip)
            out.append(pix.tobytes("png"))
        return out
    finally:
        doc.close()


def _render_all_regions(data: bytes, page_indices: list[int], dpi: int = 300) -> list[tuple[int, int, bytes]]:
    """Render every requested page into its column region(s). Returns work-units
    (page_index, region_index, png_bytes) in reading order."""
    units: list[tuple[int, int, bytes]] = []
    for i in page_indices:
        regions = _render_page_regions(data, i, dpi)
        for r_idx, png in enumerate(regions):
            units.append((i, r_idx, png))
    return units


def _extract_text_from_docx(data: bytes) -> str:
    import docx
    document = docx.Document(BytesIO(data))
    paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def _find_soffice() -> str | None:
    """Locate the headless LibreOffice binary used to convert DOCX → PDF."""
    for name in ("soffice", "libreoffice", "soffice.exe"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/usr/bin/soffice",
        "/usr/bin/libreoffice",
        "/opt/libreoffice/program/soffice",
    ):
        if os.path.exists(candidate):
            return candidate
    return None


def _docx_to_pdf_bytes(data: bytes) -> bytes:
    """Convert DOCX bytes to PDF bytes via headless LibreOffice.

    Used only for Preeti/legacy-font (or otherwise non-Unicode) Word documents, whose
    text layer is unusable — rendering to PDF lets the SAME vision-OCR pipeline read the
    glyphs. Raises a clear error if LibreOffice is missing or the conversion fails (the
    job then fails honestly rather than ingesting garbage).
    """
    soffice = _find_soffice()
    if not soffice:
        raise RuntimeError(
            "LibreOffice (soffice) is required to OCR a Preeti/scanned Word document but was "
            "not found on this worker. Install LibreOffice or re-upload the document as a PDF."
        )
    with tempfile.TemporaryDirectory() as tmp:
        in_path = os.path.join(tmp, "input.docx")
        with open(in_path, "wb") as fh:
            fh.write(data)
        # Isolate the LO user profile per conversion so concurrent jobs don't fight over
        # the shared default profile lock.
        profile_url = Path(os.path.join(tmp, "lo_profile")).as_uri()
        try:
            proc = subprocess.run(
                [
                    soffice, "--headless", "--norestore", "--nolockcheck",
                    f"-env:UserInstallation={profile_url}",
                    "--convert-to", "pdf", "--outdir", tmp, in_path,
                ],
                capture_output=True, timeout=180,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("LibreOffice timed out converting the Word document to PDF.") from exc
        out_path = os.path.join(tmp, "input.pdf")
        if not os.path.exists(out_path):
            stderr = proc.stderr.decode("utf-8", "ignore")[:300] if proc.stderr else ""
            raise RuntimeError(
                f"LibreOffice failed to convert the Word document to PDF "
                f"(exit {proc.returncode}). {stderr}"
            )
        with open(out_path, "rb") as fh:
            return fh.read()


async def _ocr_pdf_bytes(file_bytes: bytes, step_cb) -> str:
    """OCR every page of a PDF (given as bytes) with the typed-vision model and return the
    concatenated text. A page that cannot be read is SKIPPED — never backfilled with the raw
    text layer / a Preeti decode (that content is garbage for this corpus). The vision model's
    only manipulation of a real page is stripping running headers/footers/watermarks; it never
    rejects, rewords, or summarises content.

    `step_cb(progress:int, msg:str)` is an async progress callback. Shared by direct-PDF
    uploads and by Preeti/scanned DOCX after the DOCX → PDF conversion.
    """
    page_texts_raw: list[str] = await asyncio.to_thread(_extract_pdf_pages_text, file_bytes)
    total_pages = len(page_texts_raw)
    # Scanned/legacy-font knowledge PDFs are TYPED text → Azure gpt-5 typed vision OCR
    # (not Gemini, which is reserved for handwriting; not gpt-5.5).
    provider = get_provider("vision_typed")

    # Classification still runs (cheap) — only to annotate logs; FORCE_OCR_ALL_PAGES makes
    # _needs_vision() true for every page, so all pages are OCR'd.
    classifications: list[str] = [_classify_page_text(t) for t in page_texts_raw]
    vision_pages: list[int] = [i for i, cls in enumerate(classifications) if _needs_vision(cls)]

    # Render each page into high-fidelity PNG region(s): dense two-column pages are split at
    # their whitespace gutter (≈2× pixels/glyph + correct left→right order); single-column pages
    # stay whole. One work-unit per region: (page_index, region_index, png_bytes).
    units: list[tuple[int, int, bytes]] = []
    if vision_pages:
        summary = ", ".join(f"p{i+1}={classifications[i]}" for i in vision_pages)
        await step_cb(22, f"Vision OCR — rendering {len(vision_pages)}/{total_pages} pages ({summary[:80]})…")
        units = await asyncio.to_thread(_render_all_regions, file_bytes, vision_pages, 300)
        split_pages = sorted({p for p, r, _ in units if r > 0})
        if split_pages:
            logger.info(
                "Column-split fired on %d page(s): %s",
                len(split_pages), ", ".join(f"p{p+1}" for p in split_pages),
            )

    total_regions = len(units)
    ocr_sem = asyncio.Semaphore(6)
    VISION_OCR_ATTEMPTS = 3

    failed_regions: list[tuple[int, int]] = []   # unreadable after retries / 404 — skipped

    async def _process_one_region(page_i: int, region_i: int, png: bytes) -> tuple[int, int, str]:
        tag = f"page {page_i + 1} region {region_i + 1}"
        async with ocr_sem:
            last_err: str | None = None
            for attempt in range(1, VISION_OCR_ATTEMPTS + 1):
                try:
                    vision_result = await provider.generate_with_image(
                        VISION_EXTRACT_PROMPT, png, schema=None
                    )
                    ocr_text = (vision_result.get("text") or "").strip()
                    if ocr_text:
                        logger.info("Vision OCR %s: %d chars", tag, len(ocr_text))
                        return (page_i, region_i, ocr_text)
                    last_err = "empty text"
                    logger.warning(
                        "Vision OCR %s returned empty text (attempt %d/%d)",
                        tag, attempt, VISION_OCR_ATTEMPTS,
                    )
                except Exception as exc:
                    last_err = str(exc)
                    if "404" in last_err:
                        # The deployment cannot accept images at all — retrying is pointless.
                        # Skip the region; if every region 404s the job fails loudly below.
                        logger.error(
                            "Vision not supported by this deployment (404 on %s) — "
                            "cannot OCR; region skipped", tag,
                        )
                        failed_regions.append((page_i, region_i))
                        return (page_i, region_i, "")
                    logger.warning(
                        "Vision OCR failed %s (attempt %d/%d): %s",
                        tag, attempt, VISION_OCR_ATTEMPTS, exc,
                    )
            logger.error(
                "Vision OCR exhausted %d attempts on %s (%s) — region skipped "
                "(NOT ingesting raw/garbage text)",
                VISION_OCR_ATTEMPTS, tag, last_err,
            )
            failed_regions.append((page_i, region_i))
            return (page_i, region_i, "")

    await step_cb(23, f"Vision OCR — processing {total_regions} region(s) of {len(vision_pages)} page(s) in parallel…")
    region_results = await asyncio.gather(
        *[_process_one_region(p, r, png) for p, r, png in units]
    )
    # Regions left→right within a page, pages in order.
    region_results = sorted(region_results, key=lambda x: (x[0], x[1]))
    text = "\n\n".join(t for _, _, t in region_results if t)

    # A page is unreadable only if ALL its regions failed.
    regions_by_page: dict[int, list[int]] = {}
    for p, r, _ in units:
        regions_by_page.setdefault(p, []).append(r)
    failed_by_page: dict[int, set[int]] = {}
    for p, r in failed_regions:
        failed_by_page.setdefault(p, set()).add(r)
    unreadable_pages = [p for p, regs in regions_by_page.items() if failed_by_page.get(p, set()) >= set(regs)]

    extracted_regions = total_regions - len(failed_regions)
    summary = (
        f"OCR complete — {extracted_regions}/{total_regions} region(s) extracted, "
        f"{len(unreadable_pages)} unreadable page(s) of {total_pages}."
    )
    logger.info(summary)
    await step_cb(24, summary)
    return text


def _split_into_sections(text: str, max_chars: int = 8000) -> list[str]:
    """Split long text into overlapping sections at paragraph boundaries."""
    paragraphs = text.split("\n\n")
    sections: list[str] = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) + 2 > max_chars and current:
            sections.append(current.strip())
            # 20% overlap: keep last ~1600 chars of previous section
            overlap_start = max(0, len(current) - max_chars // 5)
            current = current[overlap_start:] + "\n\n" + para
        else:
            current = (current + "\n\n" + para) if current else para

    if current.strip():
        sections.append(current.strip())

    return sections or [text]


class KnowledgeProcessingAgent:
    async def process(
        self,
        document_id: str,
        job_id: str,
        db: AsyncSession | None = None,
    ) -> None:
        """Process a knowledge document into embedded, Pinecone-indexed chunks.

        DB-session lifecycle (the reason this is structured in three phases):

          1. LOAD   — a short session reads the document/file/syllabus metadata,
                      marks the doc `processing`, then CLOSES.
          2. WORK   — the long AI/OCR/embedding/Pinecone work runs holding NO DB
                      session. Pooled asyncpg connections are dropped server-side
                      (Neon) when left idle for minutes, so we must not pin one
                      across this phase — that is the root of the historic
                      "connection is closed" failure at the final commit.
          3. SAVE   — a FRESH session inserts chunks in batches and flips the doc
                      to `completed`. Made idempotent so a Celery retry that
                      re-enters this phase does not duplicate vectors/chunks.

        The `db` argument is intentionally ignored: every phase opens its own
        short-lived session via `AsyncSessionLocal()`, so this task never reuses
        a global/long-lived session.
        """
        job_uuid = uuid.UUID(job_id)
        doc_uuid = uuid.UUID(document_id)

        async def _step(status: JobStatus, progress: int, step: str) -> None:
            # Always use a fresh session so a failed step update never
            # corrupts the main data session (asyncpg marks a session
            # IN_FAILED_TRANSACTION on any error; a shared session would
            # then reject all subsequent data commits). A progress update is
            # cosmetic, so a transient connection drop here is retried a couple
            # of times and then swallowed — it must never fail the job.
            async def _do() -> None:
                async with AsyncSessionLocal() as step_db:
                    await update_job(step_db, job_uuid, status=status, progress=progress, step=step)

            try:
                await _db_op_with_retry(_do, attempts=3, label=f"progress {progress}%")
            except Exception as step_exc:
                logger.warning("Could not update job progress (%d%% — %s): %s", progress, step, step_exc)

        try:
            await _step(JobStatus.processing, 5, "Loading document record…")

            # ── PHASE 1: LOAD METADATA (short session, then close) ──────────────
            # Capture everything the long work phase needs into plain locals so we
            # hold no ORM objects (and no DB connection) across the AI work.
            async with AsyncSessionLocal() as load_db:
                result = await load_db.execute(
                    select(KnowledgeDocument).where(KnowledgeDocument.id == doc_uuid)
                )
                doc = result.scalar_one_or_none()
                if not doc:
                    await update_job(load_db, job_uuid, status=JobStatus.failed, error="Document not found.")
                    return

                # Mark document as processing
                doc.processing_status = "processing"

                exam_id = doc.exam_id
                document_type = doc.document_type
                custom_instruction = doc.custom_instruction
                doc_topic = doc.topic
                doc_subtopic = doc.subtopic
                display_name = doc.display_name
                file_id = doc.file_id

                # exam_type drives the chunk metadata; chapter is the admin-entered
                # real value (no more hardcoded chapter-by-usage-type).
                exam = (await load_db.execute(select(Exam).where(Exam.id == exam_id))).scalar_one_or_none()
                exam_type = exam.exam_type if exam else ""
                doc_chapter = doc.chapter or ""

                # Load the OFFICIAL syllabus tree for chunk→syllabus mapping + validation
                # (CLAUDE.md §8). Chapter is the PRIMARY retrieval dimension, mapped per chunk:
                #   • Single-chapter upload (admin picked a chapter) → LOCKED mode: scope the tree
                #     to that chapter (get_chapter_tree(chapter=…)) so chunks can only carry that
                #     chapter's topics, and the chapter itself is forced to it below.
                #   • Whole-book upload (no chapter) → the AI classifies EACH chunk to a
                #     chapter+topic+subtopic from the full exam tree, so a book spanning many
                #     official chapters is filed correctly (never all under one/blank chapter).
                # get_chapter_tree lives in the video service and is the shared builder used by the
                # tutor + subjective workers; import lazily to avoid an import cycle at module load.
                from app.modules.video.service import get_chapter_tree, resolve_syllabus_labels
                (
                    syllabus_tree_text,
                    valid_topics,
                    valid_subtopics,
                    valid_chapters,
                    topic_to_chapter,
                ) = await get_chapter_tree(load_db, exam_id, chapter=doc_chapter or None)
                mapping_block = (
                    MAPPING_BLOCK_LOCKED.format(chapter=doc_chapter)
                    if doc_chapter
                    else MAPPING_BLOCK_WHOLE_BOOK
                )

                # Load file record
                file_result = await load_db.execute(select(File).where(File.id == file_id))
                file_record = file_result.scalar_one_or_none()
                if not file_record:
                    raise RuntimeError("File record not found.")
                r2_key = file_record.r2_key
                mime = file_record.mime_type

                await load_db.commit()
            # load_db is now closed — no DB session is held during the work below.

            # ── PHASE 2: LONG AI WORK (no DB session held) ──────────────────────
            await _step(JobStatus.processing, 10, "Downloading file from storage…")

            file_bytes = await asyncio.to_thread(get_r2().download_fileobj, r2_key)

            await _step(JobStatus.processing, 20, "Extracting text…")

            # Async progress callback shared with the OCR helper.
            async def _ocr_step(progress: int, msg: str) -> None:
                await _step(JobStatus.processing, progress, msg)

            if "pdf" in mime:
                # OCR every page (FORCE_OCR_ALL_PAGES) — this corpus is all scanned or
                # Preeti-font, so the text layer is unusable. See `_ocr_pdf_bytes`.
                raw_text = await _ocr_pdf_bytes(file_bytes, _ocr_step)

            elif "word" in mime or "docx" in mime or "msword" in mime:
                # A Word document may be real Unicode OR legacy Preeti-encoded. Extract the
                # text layer and classify it the same way as a PDF page:
                #   • valid_unicode → real Nepali/English text → use it directly.
                #   • anything else (Preeti/legacy, empty, broken) → the text is garbage;
                #     render the DOCX to PDF (LibreOffice) and run the SAME vision-OCR path.
                raw_docx_text = await asyncio.to_thread(_extract_text_from_docx, file_bytes)
                docx_cls = _classify_page_text(raw_docx_text)
                if docx_cls == "valid_unicode":
                    logger.info("DOCX is valid Unicode → using extracted text directly")
                    raw_text = raw_docx_text
                else:
                    logger.info(
                        "DOCX classified '%s' (not valid Unicode) → converting to PDF for OCR",
                        docx_cls,
                    )
                    await _step(JobStatus.processing, 18, "Converting Word document to PDF for OCR…")
                    pdf_bytes = await asyncio.to_thread(_docx_to_pdf_bytes, file_bytes)
                    raw_text = await _ocr_pdf_bytes(pdf_bytes, _ocr_step)
            else:
                raise RuntimeError(f"Unsupported file type for text extraction: {mime}")

            if not raw_text.strip():
                raise RuntimeError(
                    "No text could be extracted from the document. "
                    "Check that the PDF is readable and not password-protected, "
                    "and that the vision model deployment supports image inputs."
                )

            # [DEBUG] Stage 2 — full transcribed text as it leaves OCR (before any
            # sectioning/extraction). Compare against the source PDF to spot rephrasing.
            _debug_dump(document_id, "01_raw_ocr.txt", raw_text)

            sections = _split_into_sections(raw_text, max_chars=8000)

            # [DEBUG] Stage 3 — how the raw text was split into overlapping sections.
            # A model_qa pair straddling a boundary here is where "incomplete question"
            # chunks come from. Each section is delimited with its index + char count.
            _debug_dump(
                document_id,
                "02_sections.txt",
                "\n".join(
                    f"\n{'=' * 70}\n=== SECTION {i} ({len(s)} chars) ===\n{'=' * 70}\n{s}"
                    for i, s in enumerate(sections)
                ),
            )
            # Semantic chunking tier is configurable via settings.CHUNKING_MODEL_TIER
            # (.env): default gpt-5 ("thinking") for best Nepali segmentation/verbatim
            # fidelity since chunking is a one-time per-document cost, or gpt-5-mini
            # ("fast") for the cheaper option. See ai/model_router.py::get_provider.
            # (provider.embed below ignores the chat tier and uses the embedding deployment.)
            provider = get_provider("chunking")

            # Parallel chunking: max 6 concurrent text-model calls
            chunk_sem = asyncio.Semaphore(6)

            async def _chunk_one_section(i: int, section: str) -> list[dict]:
                async with chunk_sem:
                    prompt = CHUNK_PROMPT.format(
                        document_type=document_type,
                        exam_type=exam_type,
                        custom_instruction=custom_instruction or "None",
                        syllabus_tree=syllabus_tree_text,
                        mapping_block=mapping_block,
                        text=section,
                    )
                    try:
                        result_json = await provider.generate_text(prompt, schema={"type": "object"})
                    except Exception as exc:
                        logger.warning("AI chunking failed for section %d: %s", i, exc)
                        # Keep the section as one un-mapped chunk; the validation pass below
                        # resolves its chapter/topic (locked chapter is forced there).
                        return [{
                            "content": section,
                            "content_type": "concept_explanation",
                            "chapter": doc_chapter or None,
                            "topic": doc_topic or None,
                            "subtopic": doc_subtopic or None,
                            "language": "nepali_english_mixed",
                        }]
                    # [DEBUG] Stage 4 — raw model output for THIS section (includes the
                    # leading `section_analysis` with `form`/`chapters_involved` and every
                    # chunk's assigned chapter/topic before syllabus validation).
                    _debug_dump(
                        document_id,
                        f"03_extract_section_{i:02d}_raw.json",
                        json.dumps(result_json, ensure_ascii=False, indent=2),
                    )
                    # Preferred shape: {"section_analysis": {...}, "chunks": [...]}. Fall back to
                    # a bare array or any dict-with-list-value so a model that omits the wrapper
                    # (or emits the old shape) still works.
                    if isinstance(result_json, dict):
                        chunks = result_json.get("chunks")
                        if isinstance(chunks, list):
                            return chunks
                        for val in result_json.values():
                            if isinstance(val, list):
                                return val
                        return []
                    if isinstance(result_json, list):
                        return result_json
                    return []

            # `model_qa` documents (question papers with model answers) are extracted as
            # QUESTION↔ANSWER pairs — one pair per chunk (content = answer, plus "question") —
            # instead of semantic prose chunks. Everything downstream (syllabus validation,
            # embedding, save) is shared; only the per-section extractor differs.
            is_model_qa = document_type == "model_qa"

            async def _extract_qa_pairs_section(i: int, section: str) -> list[dict]:
                async with chunk_sem:
                    prompt = QA_EXTRACT_PROMPT.format(
                        exam_type=exam_type,
                        custom_instruction=custom_instruction or "None",
                        syllabus_tree=syllabus_tree_text,
                        mapping_block=mapping_block,
                        text=section,
                    )
                    try:
                        result_json = await provider.generate_text(prompt, schema={"type": "object"})
                    except Exception as exc:
                        logger.warning("AI Q&A extraction failed for section %d: %s", i, exc)
                        _debug_dump(document_id, f"03_extract_section_{i:02d}_ERROR.txt", str(exc))
                        return []
                    # [DEBUG] Stage 4 — raw model output for THIS section, before any
                    # filtering. Shows exactly what the extractor returned (rephrased text,
                    # dropped/partial pairs, missing question fields) per section.
                    _debug_dump(
                        document_id,
                        f"03_extract_section_{i:02d}_raw.json",
                        json.dumps(result_json, ensure_ascii=False, indent=2),
                    )
                    pairs = result_json.get("pairs") if isinstance(result_json, dict) else None
                    if not isinstance(pairs, list):
                        return []
                    chunks: list[dict] = []
                    for p in pairs:
                        if not isinstance(p, dict):
                            continue
                        question = (p.get("question") or "").strip()
                        answer = (p.get("answer") or "").strip()
                        if not question or not answer:
                            continue
                        chunks.append({
                            "content": answer,
                            "question": question,
                            "embed_text": f"{question}\n{answer}",
                            "content_type": "model_qa",
                            "chapter": p.get("chapter"),
                            "topic": p.get("topic"),
                            "subtopic": p.get("subtopic"),
                            "language": p.get("language", "nepali_english_mixed"),
                        })
                    return chunks

            _section_fn = _extract_qa_pairs_section if is_model_qa else _chunk_one_section
            _verb = "Extracting Q&A pairs from" if is_model_qa else "Chunking"
            await _step(JobStatus.processing, 30, f"{_verb} {len(sections)} sections in parallel…")
            section_results = await asyncio.gather(
                *[_section_fn(i, s) for i, s in enumerate(sections)]
            )
            all_chunks: list[dict] = [chunk for sr in section_results for chunk in sr]

            await _step(JobStatus.processing, 55, f"Embedding {len(all_chunks)} chunks…")

            # Post-validate every chunk's AI-assigned (chapter, topic, subtopic) against the
            # official syllabus. Chapter is resolved deterministically from the validated topic
            # via topic_to_chapter (never trusted raw from the model); in locked mode it is
            # forced to doc_chapter. An unmappable chunk (whole-book, no matching topic) keeps a
            # null chapter/topic — still embedded + retrievable in broad queries, never mis-filed.
            for _c in all_chunks:
                _t, _sub, _ch = resolve_syllabus_labels(
                    topic=_c.get("topic"),
                    subtopic=_c.get("subtopic"),
                    chapter=_c.get("chapter"),
                    valid_topics=valid_topics,
                    valid_subtopics=valid_subtopics,
                    valid_chapters=valid_chapters,
                    topic_to_chapter=topic_to_chapter,
                    locked_chapter=(doc_chapter or None),
                )
                _c["topic"] = _t
                _c["subtopic"] = _sub
                _c["chapter"] = _ch

            # [DEBUG] Stage 5 — the final chunk set after syllabus validation, exactly as it
            # will be embedded/saved. For model_qa: `question` + `embed_text` (what gets
            # embedded) + `content` (the answer stored in knowledge_chunks). This is where you
            # confirm whether each chunk is a COMPLETE question with its question preserved.
            _debug_dump(
                document_id,
                "04_final_chunks.json",
                json.dumps(all_chunks, ensure_ascii=False, indent=2),
            )

            # For model_qa chunks embed question+answer (embed_text) so retrieval matches on the
            # question; prose chunks have no embed_text and embed their content as before.
            chunk_texts = [c.get("embed_text") or c.get("content", "") for c in all_chunks]
            embeddings = await provider.embed(chunk_texts)

            # Build vectors + plain chunk payloads (NOT ORM objects — no session
            # is open yet). Vector IDs are DETERMINISTIC on (document_id, index)
            # so a retry re-upserts the same IDs instead of creating duplicates.
            vectors: list[dict] = []
            chunk_payloads: list[dict] = []

            for idx, (chunk, embedding) in enumerate(zip(all_chunks, embeddings)):
                vector_id = f"{doc_uuid}:{idx}"

                # Per-chunk official-syllabus labels (validated above). The admin's doc-level
                # topic/subtopic only backfill in LOCKED mode (doc_chapter set) where they belong
                # to that chapter; in whole-book mode a null chapter/topic stays null so an
                # unmappable chunk is never given a topic without a chapter.
                resolved_chapter = chunk.get("chapter")
                resolved_topic = chunk.get("topic") or (doc_topic if doc_chapter else None)
                resolved_subtopic = chunk.get("subtopic") or (doc_subtopic if doc_chapter else None)

                metadata = {
                    "document_id": str(doc_uuid),
                    "document_name": display_name,
                    "document_type": document_type,
                    "exam_id": str(exam_id),
                    "exam_type": exam_type,
                    "chapter": resolved_chapter or "",
                    "topic": resolved_topic or "",
                    "subtopic": resolved_subtopic or "",
                    "language": chunk.get("language", "nepali_english_mixed"),
                    "content_type": chunk.get("content_type", "concept_explanation"),
                    "quality_status": "processed",
                    "chunk_index": idx,
                }
                # model_qa chunks carry the source question so retrieval can surface
                # "the model answer to exactly this question" (stored in Pinecone metadata
                # + the knowledge_chunks.metadata JSONB — no dedicated column).
                if chunk.get("question"):
                    metadata["question"] = chunk["question"]

                vectors.append({
                    "id": vector_id,
                    "values": embedding,
                    "metadata": metadata,
                })

                chunk_payloads.append({
                    "chunk_index": idx,
                    "content": chunk.get("content", ""),
                    "content_type": chunk.get("content_type"),
                    "chapter": resolved_chapter,
                    "topic": resolved_topic,
                    "subtopic": resolved_subtopic,
                    "language": chunk.get("language", "nepali_english_mixed"),
                    "pinecone_vector_id": vector_id,
                    "metadata": metadata,
                })

            # ── PHASE 3: SAVE (fresh sessions, idempotent, batched) ─────────────
            # Idempotency: a Celery retry can re-enter this phase after a prior run
            # already wrote some vectors/chunks. Clear any prior chunks for this
            # document (and their Pinecone vectors) before writing the new set, so
            # retries never duplicate. Deterministic vector IDs additionally cause
            # same-index re-upserts to overwrite rather than accumulate.
            await _step(JobStatus.processing, 70, "Clearing any prior partial results…")

            # Each DB unit below opens its OWN fresh session and is idempotent, so
            # `_db_op_with_retry` can re-run it on a connection the server dropped
            # mid-operation (which pool_pre_ping cannot prevent on this network).

            # Clear any chunks from a prior partial/failed run; return their vector
            # ids so we can also purge them from Pinecone. Re-running just finds no
            # rows the second time — safe.
            async def _clear_old_chunks() -> list[str]:
                async with AsyncSessionLocal() as clean_db:
                    try:
                        old_res = await clean_db.execute(
                            select(KnowledgeChunk).where(KnowledgeChunk.document_id == doc_uuid)
                        )
                        old_chunks = old_res.scalars().all()
                        ids = [c.pinecone_vector_id for c in old_chunks if c.pinecone_vector_id]
                        for c in old_chunks:
                            await clean_db.delete(c)
                        await clean_db.commit()
                        return ids
                    except Exception:
                        await clean_db.rollback()
                        raise

            old_vector_ids = await _db_op_with_retry(_clear_old_chunks, label="clear old chunks")

            if old_vector_ids:
                await asyncio.to_thread(get_pinecone().delete_vectors, old_vector_ids)

            await _step(JobStatus.processing, 78, "Upserting vectors to Pinecone…")
            await asyncio.to_thread(get_pinecone().upsert_vectors, vectors)

            await _step(JobStatus.processing, 88, "Saving chunks to database…")

            # Insert chunks in small batches, each its own transaction, on a FRESH
            # short-lived session — so the final write never depends on a
            # connection that has been idle through the long AI work above. The
            # whole insert is idempotent (old chunks were just cleared and vector
            # ids are deterministic), so a mid-operation drop retries cleanly.
            BATCH_SIZE = 100

            async def _save_chunks() -> None:
                async with AsyncSessionLocal() as save_db:
                    try:
                        # Re-entrancy guard for a retry that already committed some
                        # batches before the connection dropped: start clean.
                        existing = await save_db.execute(
                            select(KnowledgeChunk.id).where(KnowledgeChunk.document_id == doc_uuid).limit(1)
                        )
                        if existing.scalar_one_or_none() is not None:
                            await save_db.execute(
                                KnowledgeChunk.__table__.delete().where(
                                    KnowledgeChunk.document_id == doc_uuid
                                )
                            )
                            await save_db.commit()

                        for start in range(0, len(chunk_payloads), BATCH_SIZE):
                            batch = chunk_payloads[start : start + BATCH_SIZE]
                            save_db.add_all([
                                KnowledgeChunk(
                                    document_id=doc_uuid,
                                    exam_id=exam_id,
                                    chunk_index=p["chunk_index"],
                                    content=p["content"],
                                    content_type=p["content_type"],
                                    chapter=p["chapter"],
                                    topic=p["topic"],
                                    subtopic=p["subtopic"],
                                    language=p["language"],
                                    pinecone_vector_id=p["pinecone_vector_id"],
                                    quality_status="processed",
                                    chunk_metadata=p["metadata"],
                                )
                                for p in batch
                            ])
                            await save_db.commit()

                        # Flip the document to completed in the same fresh session.
                        doc_res = await save_db.execute(
                            select(KnowledgeDocument).where(KnowledgeDocument.id == doc_uuid)
                        )
                        doc_row = doc_res.scalar_one_or_none()
                        if doc_row:
                            doc_row.chunk_count = len(chunk_payloads)
                            doc_row.processing_status = "completed"
                            await save_db.commit()
                    except Exception:
                        await save_db.rollback()
                        raise

            await _db_op_with_retry(_save_chunks, label="save chunks")

            # Fresh session for the completion update.
            async def _mark_done() -> None:
                async with AsyncSessionLocal() as done_db:
                    await update_job(
                        done_db,
                        job_uuid,
                        status=JobStatus.completed,
                        progress=100,
                        step="Processing complete",
                        output={"document_id": str(doc_uuid), "chunk_count": len(chunk_payloads)},
                    )

            await _db_op_with_retry(_mark_done, label="mark job done")
            logger.info("Knowledge document %s processed: %d chunks", document_id, len(chunk_payloads))

        except Exception as exc:
            logger.exception("Knowledge processing failed for document %s", document_id)

            async def _mark_failed() -> None:
                async with AsyncSessionLocal() as fresh_db:
                    res = await fresh_db.execute(
                        select(KnowledgeDocument).where(KnowledgeDocument.id == doc_uuid)
                    )
                    doc_ref = res.scalar_one_or_none()
                    if doc_ref:
                        doc_ref.processing_status = "failed"
                        await fresh_db.commit()
                    await update_job(fresh_db, job_uuid, status=JobStatus.failed, error=str(exc))

            try:
                await _db_op_with_retry(_mark_failed, attempts=3, label="mark failed")
            except Exception as cleanup_exc:
                logger.error("Failed to mark job/document as failed: %s", cleanup_exc)
            raise
