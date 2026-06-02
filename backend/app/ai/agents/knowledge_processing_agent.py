import asyncio
import json
import logging
import uuid
from collections import defaultdict
from io import BytesIO

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.core.database import AsyncSessionLocal
from app.integrations.pinecone_client import get_pinecone
from app.integrations.r2_client import get_r2
from app.modules.files.models import File
from app.modules.jobs.service import update_job
from app.modules.jobs.models import JobStatus
from app.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument
from app.modules.syllabus.models import SyllabusItem, SyllabusType

logger = logging.getLogger(__name__)

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

CHUNK_PROMPT = """You are processing educational content for semantic retrieval.
Analyze the text below and split it into meaningful, self-contained chunks.

Rules:
- Each chunk must be complete and independently understandable
- Good chunks: complete definition + explanation, complete concept block, full example, exam-focused point group
- Bad chunks: mid-sentence cuts, contextless fragments, giant multi-topic blocks
- Target 100–500 words per chunk; never cut a sentence mid-way

Context:
- Document type: {document_type}
- Content usage: {content_usage_type}
- Custom instruction: {custom_instruction}

VALID SYLLABUS TOPICS AND SUBTOPICS — use ONLY these exact strings:
{syllabus_topics}

Topic assignment rules:
- Set "topic" to the exact string from the list above that best matches the chunk content.
- Set "subtopic" to the exact string from the list above, or null if no subtopic applies.
- If the chunk content does not match any listed topic, set both to null.
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

# Hardcoded chapter names for this deployment's single-chapter vertical slice.
_CHAPTER_BY_USAGE_TYPE: dict[str, str] = {
    "objective":  "भूगोल, वातावरण र जनसंख्या",
    "subjective": "बैंकिङ्ग",
}


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
        db: AsyncSession,
    ) -> None:
        job_uuid = uuid.UUID(job_id)
        doc_uuid = uuid.UUID(document_id)

        async def _step(status: JobStatus, progress: int, step: str) -> None:
            # Always use a fresh session so a failed step update never
            # corrupts the main data session (asyncpg marks a session
            # IN_FAILED_TRANSACTION on any error; a shared session would
            # then reject all subsequent data commits).
            try:
                async with AsyncSessionLocal() as step_db:
                    await update_job(step_db, job_uuid, status=status, progress=progress, step=step)
            except Exception as step_exc:
                logger.warning("Could not update job progress (%d%% — %s): %s", progress, step, step_exc)

        try:
            await _step(JobStatus.processing, 5, "Loading document record…")

            result = await db.execute(
                select(KnowledgeDocument).where(KnowledgeDocument.id == doc_uuid)
            )
            doc = result.scalar_one_or_none()
            if not doc:
                await update_job(db, job_uuid, status=JobStatus.failed, error="Document not found.")
                return

            # Mark document as processing
            doc.processing_status = "processing"
            await db.commit()

            # Derive hardcoded chapter for this deployment's vertical slice
            hardcoded_chapter = _CHAPTER_BY_USAGE_TYPE.get(doc.content_usage_type, "")

            # Load syllabus topics/subtopics for metadata validation and prompt injection
            syl_result = await db.execute(
                select(SyllabusItem)
                .where(
                    SyllabusItem.syllabus_type == SyllabusType(doc.content_usage_type),
                    SyllabusItem.is_active == True,
                )
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
            file_result = await db.execute(select(File).where(File.id == doc.file_id))
            file_record = file_result.scalar_one_or_none()
            if not file_record:
                raise RuntimeError("File record not found.")

            await _step(JobStatus.processing, 10, "Downloading file from storage…")

            file_bytes = await asyncio.to_thread(get_r2().download_fileobj, file_record.r2_key)

            await _step(JobStatus.processing, 20, "Extracting text…")

            mime = file_record.mime_type
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
                provider = get_provider("reasoning")

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
            provider = get_provider("text")

            # Parallel chunking: max 5 concurrent text-model calls
            chunk_sem = asyncio.Semaphore(5)

            async def _chunk_one_section(i: int, section: str) -> list[dict]:
                async with chunk_sem:
                    prompt = CHUNK_PROMPT.format(
                        document_type=doc.document_type,
                        content_usage_type=doc.content_usage_type,
                        custom_instruction=doc.custom_instruction or "None",
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
                            "topic": doc.topic or "",
                            "subtopic": doc.subtopic or "",
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

            await _step(JobStatus.processing, 70, "Upserting vectors to Pinecone…")

            vectors: list[dict] = []
            chunk_records: list[KnowledgeChunk] = []
            vector_ids: list[str] = []

            for idx, (chunk, embedding) in enumerate(zip(all_chunks, embeddings)):
                vector_id = str(uuid.uuid4())
                vector_ids.append(vector_id)

                metadata = {
                    "document_id": str(doc.id),
                    "document_name": doc.display_name,
                    "document_type": doc.document_type,
                    "content_usage_type": doc.content_usage_type,
                    "chapter": hardcoded_chapter,
                    "topic": chunk.get("topic") or doc.topic or "",
                    "subtopic": chunk.get("subtopic") or doc.subtopic or "",
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

                chunk_records.append(KnowledgeChunk(
                    document_id=doc.id,
                    chunk_index=idx,
                    content=chunk.get("content", ""),
                    content_type=chunk.get("content_type"),
                    chapter=hardcoded_chapter,
                    topic=chunk.get("topic") or doc.topic,
                    subtopic=chunk.get("subtopic") or doc.subtopic,
                    language=chunk.get("language", "nepali_english_mixed"),
                    pinecone_vector_id=vector_id,
                    quality_status="processed",
                    chunk_metadata=metadata,
                ))

            await asyncio.to_thread(get_pinecone().upsert_vectors, vectors)

            await _step(JobStatus.processing, 88, "Saving chunks to database…")

            db.add_all(chunk_records)
            doc.chunk_count = len(chunk_records)
            doc.processing_status = "completed"
            await db.commit()

            # Use a fresh session for the completion update — the main data
            # session may be in a dirty state after a long run.
            async with AsyncSessionLocal() as done_db:
                await update_job(
                    done_db,
                    job_uuid,
                    status=JobStatus.completed,
                    progress=100,
                    step="Processing complete",
                    output={"document_id": str(doc.id), "chunk_count": len(chunk_records)},
                )
            logger.info("Knowledge document %s processed: %d chunks", document_id, len(chunk_records))

        except Exception as exc:
            logger.exception("Knowledge processing failed for document %s", document_id)
            try:
                async with AsyncSessionLocal() as fresh_db:
                    res = await fresh_db.execute(
                        select(KnowledgeDocument).where(KnowledgeDocument.id == doc_uuid)
                    )
                    doc_ref = res.scalar_one_or_none()
                    if doc_ref:
                        doc_ref.processing_status = "failed"
                        await fresh_db.commit()
                    await update_job(fresh_db, job_uuid, status=JobStatus.failed, error=str(exc))
            except Exception as cleanup_exc:
                logger.error("Failed to mark job/document as failed: %s", cleanup_exc)
