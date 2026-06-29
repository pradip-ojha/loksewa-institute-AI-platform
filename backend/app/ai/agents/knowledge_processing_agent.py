import asyncio
import json
import logging
import uuid
from collections import defaultdict
from io import BytesIO

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
from app.modules.syllabus.models import SyllabusItem
from app.modules.exams.models import Exam

logger = logging.getLogger(__name__)

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

PREETI_DECODE_PROMPT = """The text below was extracted from a PDF that uses a legacy Nepali font (Preeti or Kantipur).
These fonts store Devanagari glyphs mapped to ASCII codepoints, so PDF text extraction returns garbled ASCII instead of Unicode Devanagari.

Your task: Reconstruct the original Nepali (and any English) content in proper Unicode Devanagari script.

Rules:
- Output proper Unicode Devanagari Nepali for all Nepali content.
- Keep any English words/sentences exactly as they appear.
- Preserve all structure: headings on their own line, numbered lists, bullet points, paragraphs separated by blank lines.
- Do not translate, summarise, or add anything not in the original.
- If a word cannot be decoded confidently, write your best interpretation — do not skip it.

Garbled input text:
{text}

Output: the reconstructed content in proper Unicode."""

VISION_EXTRACT_PROMPT = """Extract every word of text visible on this page. Return clean, readable text only — no commentary, no explanations.

Formatting rules:
- Nepali text → proper Unicode Devanagari (e.g. नेपाल, विकास, बैंकिङ). Never return Romanised transliteration or ASCII encodings.
- English text → exactly as shown on the page.
- Mixed Nepali-English → preserve both scripts as they appear.
- Headings → output on their own line, followed by a blank line.
- Numbered lists → preserve numbers exactly (1. 2. 3. or १. २. ३.).
- Bullet lists → preserve bullets (•, -, or ▪).
- Paragraphs → separate with a blank line.
- Tables → output row by row, columns separated by |.
- Page numbers, headers/footers → skip them.
- If a word is partially illegible → write your best guess, do not skip.

Return only the extracted text."""

CHUNK_PROMPT = EXAM_CONTEXT + """

ROLE: You prepare Nepali Loksewa/banking study material for semantic retrieval. Downstream agents
(MCQ generation, answer checking, the video tutor) will fetch these chunks by meaning, so each
chunk must stand on its own and carry one coherent idea.

TASK: Split the text below into meaningful, self-contained chunks and tag each one.

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
- CHAPTER (the primary scope — this whole document belongs to it): {chapter}
- Custom instruction: {custom_instruction}

CHAPTER IS THE PRIMARY RETRIEVAL KEY. This document was uploaded under the chapter above, so every
chunk belongs to that chapter. Topic/subtopic are a SECONDARY, finer label *within* that chapter —
they only narrow within it, they never override it.

VALID SYLLABUS TOPICS AND SUBTOPICS for THIS CHAPTER — use ONLY these exact strings:
{syllabus_topics}

Topic assignment rules:
- The chunk's chapter is already fixed ({chapter}); you only choose its topic/subtopic within it.
- Set "topic" to the exact string from the list above that best matches the chunk content.
- Set "subtopic" to the exact string from the list above, or null if no subtopic applies.
- If the chunk content does not match any listed topic, set both to null (it still belongs to the chapter).
- Never invent topic or subtopic strings not in the list above.

Return a JSON object with a single key "chunks" whose value is an array. Each item must have:
  "content"      : the chunk text (string)
  "content_type" : one of [concept_explanation, definition, example, exam_point, procedure, comparison, list_items, summary]
  "topic"        : exact topic string from the list, or null
  "subtopic"     : exact subtopic string from the list, or null
  "language"     : "english" | "nepali" | "nepali_english_mixed"

Example format:
{{"chunks": [{{"content": "...", "content_type": "definition", "topic": "...", "subtopic": null, "language": "nepali"}}]}}

TEXT TO CHUNK:
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


def _needs_vision(classification: str) -> bool:
    return classification != "valid_unicode"


# ── PDF helpers ────────────────────────────────────────────────────────────────

def _extract_pdf_pages_text(data: bytes) -> list[str]:
    """Extract raw text from each PDF page. Returns one string per page."""
    import fitz
    doc = fitz.open(stream=data, filetype="pdf")
    texts = [page.get_text() for page in doc]
    doc.close()
    return texts


def _render_page_jpeg(data: bytes, page_index: int, dpi: int = 250) -> bytes:
    """Render a single PDF page to JPEG bytes at the given DPI."""
    import fitz
    doc = fitz.open(stream=data, filetype="pdf")
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = doc[page_index].get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    jpeg = pix.tobytes("jpeg")
    doc.close()
    return jpeg


def _render_pages_jpeg(data: bytes, page_indices: list[int], dpi: int = 250) -> dict[int, bytes]:
    """Open PDF once and render all requested pages. Returns {page_index: jpeg_bytes}."""
    import fitz
    doc = fitz.open(stream=data, filetype="pdf")
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    out: dict[int, bytes] = {}
    for i in page_indices:
        pix = doc[i].get_pixmap(matrix=mat, colorspace=fitz.csRGB)
        out[i] = pix.tobytes("jpeg")
    doc.close()
    return out


def _extract_text_from_docx(data: bytes) -> str:
    import docx
    document = docx.Document(BytesIO(data))
    paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


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

                # Load syllabus topics/subtopics for metadata validation and prompt injection.
                # CHAPTER IS PRIMARY: when the document is uploaded under a chapter, scope the
                # valid topics to THAT chapter so chunks can't be mis-tagged with a topic from a
                # different chapter of the same exam (cross-chapter leakage). With no chapter we
                # fall back to the whole exam tree.
                _syl_where = [
                    SyllabusItem.exam_id == exam_id,
                    SyllabusItem.is_active == True,
                ]
                if doc_chapter:
                    _syl_where.append(SyllabusItem.chapter == doc_chapter)
                syl_result = await load_db.execute(
                    select(SyllabusItem)
                    .where(*_syl_where)
                    .order_by(SyllabusItem.sort_order)
                )
                syllabus_items = syl_result.scalars().all()
                valid_topics: set[str] = {item.topic for item in syllabus_items}
                valid_subtopics: set[str] = {item.subtopic for item in syllabus_items if item.subtopic}
                _topic_sub_map: dict[str, list[str]] = defaultdict(list)
                for _item in syllabus_items:
                    if _item.subtopic:
                        _topic_sub_map[_item.topic].append(_item.subtopic)
                    elif _item.topic not in _topic_sub_map:
                        _topic_sub_map[_item.topic] = []
                _lines: list[str] = []
                for _topic, _subs in _topic_sub_map.items():
                    _lines.append(f"- {_topic}")
                    for _sub in _subs:
                        _lines.append(f"  - {_sub}")
                syllabus_topics_block = "\n".join(_lines)

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

            if "pdf" in mime:
                # ── Per-page smart extraction ──────────────────────────────────
                # 1. PyMuPDF extracts text from all pages first (fast, no API cost).
                # 2. Each page is classified: valid_unicode | legacy_font | empty | broken.
                # 3. Only pages that need vision are sent to the vision model (250 DPI JPEG).
                # This minimises API calls while handling legacy Preeti/Kantipur fonts,
                # scanned PDFs, and corrupted text layers correctly.

                page_texts_raw: list[str] = await asyncio.to_thread(
                    _extract_pdf_pages_text, file_bytes
                )
                total_pages = len(page_texts_raw)
                # Scanned/legacy-font knowledge PDFs are TYPED text → Azure gpt-5 typed
                # vision OCR (not Gemini, which is reserved for handwriting; not gpt-5.5).
                provider = get_provider("vision_typed")

                # First pass: classify each page
                classifications: list[str] = [
                    _classify_page_text(t) for t in page_texts_raw
                ]
                vision_pages: list[int] = [
                    i for i, cls in enumerate(classifications) if _needs_vision(cls)
                ]

                # Pre-render all vision pages in one fitz session before parallel dispatch
                jpeg_map: dict[int, bytes] = {}
                if vision_pages:
                    summary = ", ".join(
                        f"p{i+1}={classifications[i]}" for i in vision_pages
                    )
                    await _step(
                        JobStatus.processing,
                        22,
                        f"Vision OCR — rendering {len(vision_pages)}/{total_pages} pages "
                        f"({summary[:80]})…",
                    )
                    jpeg_map = await asyncio.to_thread(
                        _render_pages_jpeg, file_bytes, vision_pages, 250
                    )

                # Parallel OCR/decode: max 3 concurrent reasoning-model calls
                ocr_sem = asyncio.Semaphore(3)
                vision_unsupported = [False]  # list so nested async fn can mutate via index

                async def _process_one_page(i: int, cls: str) -> tuple[int, str]:
                    if not _needs_vision(cls):
                        return (i, page_texts_raw[i].strip())
                    async with ocr_sem:
                        # Primary: vision OCR
                        if not vision_unsupported[0]:
                            try:
                                vision_result = await provider.generate_with_image(
                                    VISION_EXTRACT_PROMPT, jpeg_map[i], schema=None
                                )
                                ocr_text = vision_result.get("text", "").strip()
                                if ocr_text:
                                    logger.info(
                                        "Vision OCR page %d (%s): %d chars", i + 1, cls, len(ocr_text)
                                    )
                                    return (i, ocr_text)
                                else:
                                    logger.warning("Vision OCR page %d returned empty text", i + 1)
                            except Exception as exc:
                                if "404" in str(exc):
                                    logger.warning(
                                        "Vision not supported by this deployment (404 on page %d) — "
                                        "switching to text-based fallback for remaining pages",
                                        i + 1,
                                    )
                                    vision_unsupported[0] = True
                                else:
                                    logger.warning("Vision OCR failed page %d: %s", i + 1, exc)
                        # Fallback: Preeti decode for legacy-font pages
                        if cls == "legacy_font" and page_texts_raw[i].strip():
                            try:
                                decode_prompt = PREETI_DECODE_PROMPT.format(
                                    text=page_texts_raw[i].strip()[:6000]
                                )
                                decode_result = await provider.generate_text(decode_prompt, schema=None)
                                decoded = decode_result.get("text", "").strip()
                                if decoded:
                                    logger.info("Preeti decode page %d: %d chars", i + 1, len(decoded))
                                    return (i, decoded)
                            except Exception as exc:
                                logger.warning("Preeti decode failed page %d: %s", i + 1, exc)
                        # Last resort: raw extracted text
                        if page_texts_raw[i].strip():
                            logger.warning(
                                "Page %d (%s): using raw extracted text as last resort", i + 1, cls
                            )
                            return (i, page_texts_raw[i].strip())
                        return (i, "")

                await _step(
                    JobStatus.processing, 23,
                    f"Vision OCR — processing {len(vision_pages)}/{total_pages} pages in parallel…",
                )
                page_results = await asyncio.gather(
                    *[_process_one_page(i, cls) for i, cls in enumerate(classifications)]
                )
                page_results = sorted(page_results, key=lambda x: x[0])
                final_page_texts = [text for _, text in page_results]

                raw_text = "\n\n".join(t for t in final_page_texts if t)

            elif "word" in mime or "docx" in mime or "msword" in mime:
                raw_text = await asyncio.to_thread(_extract_text_from_docx, file_bytes)
            else:
                raise RuntimeError(f"Unsupported file type for text extraction: {mime}")

            if not raw_text.strip():
                raise RuntimeError(
                    "No text could be extracted from the document. "
                    "Check that the PDF is readable and not password-protected, "
                    "and that the vision model deployment supports image inputs."
                )

            sections = _split_into_sections(raw_text, max_chars=8000)
            # Semantic chunking is lower-intelligence work → gpt-5-mini (fast tier).
            # (provider.embed below ignores the chat tier and uses the embedding deployment.)
            provider = get_provider("chunking")

            # Parallel chunking: max 5 concurrent text-model calls
            chunk_sem = asyncio.Semaphore(5)

            async def _chunk_one_section(i: int, section: str) -> list[dict]:
                async with chunk_sem:
                    prompt = CHUNK_PROMPT.format(
                        document_type=document_type,
                        exam_type=exam_type,
                        chapter=doc_chapter or "(unspecified)",
                        custom_instruction=custom_instruction or "None",
                        syllabus_topics=syllabus_topics_block,
                        text=section,
                    )
                    try:
                        result_json = await provider.generate_text(prompt, schema={"type": "array"})
                    except Exception as exc:
                        logger.warning("AI chunking failed for section %d: %s", i, exc)
                        return [{
                            "content": section,
                            "content_type": "concept_explanation",
                            "topic": doc_topic or "",
                            "subtopic": doc_subtopic or "",
                            "language": "nepali_english_mixed",
                        }]
                    if isinstance(result_json, list):
                        return result_json
                    if isinstance(result_json, dict):
                        for val in result_json.values():
                            if isinstance(val, list):
                                return val
                    return []

            await _step(JobStatus.processing, 30, f"Chunking {len(sections)} sections in parallel…")
            section_results = await asyncio.gather(
                *[_chunk_one_section(i, s) for i, s in enumerate(sections)]
            )
            all_chunks: list[dict] = [chunk for sr in section_results for chunk in sr]

            await _step(JobStatus.processing, 55, f"Embedding {len(all_chunks)} chunks…")

            # Post-validate AI-assigned topics against the loaded syllabus
            for _c in all_chunks:
                _ai_topic = _c.get("topic")
                _ai_subtopic = _c.get("subtopic")
                if _ai_topic not in valid_topics:
                    _c["topic"] = None
                    _c["subtopic"] = None
                elif _ai_subtopic is not None and _ai_subtopic not in valid_subtopics:
                    _c["subtopic"] = None

            chunk_texts = [c.get("content", "") for c in all_chunks]
            embeddings = await provider.embed(chunk_texts)

            # Build vectors + plain chunk payloads (NOT ORM objects — no session
            # is open yet). Vector IDs are DETERMINISTIC on (document_id, index)
            # so a retry re-upserts the same IDs instead of creating duplicates.
            vectors: list[dict] = []
            chunk_payloads: list[dict] = []

            for idx, (chunk, embedding) in enumerate(zip(all_chunks, embeddings)):
                vector_id = f"{doc_uuid}:{idx}"

                metadata = {
                    "document_id": str(doc_uuid),
                    "document_name": display_name,
                    "document_type": document_type,
                    "exam_id": str(exam_id),
                    "exam_type": exam_type,
                    "chapter": doc_chapter,
                    "topic": chunk.get("topic") or doc_topic or "",
                    "subtopic": chunk.get("subtopic") or doc_subtopic or "",
                    "language": chunk.get("language", "nepali_english_mixed"),
                    "content_type": chunk.get("content_type", "concept_explanation"),
                    "quality_status": "processed",
                    "chunk_index": idx,
                }

                vectors.append({
                    "id": vector_id,
                    "values": embedding,
                    "metadata": metadata,
                })

                chunk_payloads.append({
                    "chunk_index": idx,
                    "content": chunk.get("content", ""),
                    "content_type": chunk.get("content_type"),
                    "topic": chunk.get("topic") or doc_topic,
                    "subtopic": chunk.get("subtopic") or doc_subtopic,
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
                                    chapter=doc_chapter or None,
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
