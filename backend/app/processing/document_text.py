"""Shared text extraction utilities for PDF and DOCX files."""
import io
import logging

logger = logging.getLogger(__name__)


def extract_text_from_pdf(data: bytes) -> str:
    try:
        import fitz  # PyMuPDF
        with fitz.open(stream=data, filetype="pdf") as doc:
            return "\n".join(page.get_text() for page in doc)
    except Exception as exc:
        logger.warning("PDF text extraction failed: %s", exc)
        return ""


def extract_text_from_docx(data: bytes) -> str:
    try:
        from docx import Document
        doc = Document(io.BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception as exc:
        logger.warning("DOCX text extraction failed: %s", exc)
        return ""


def extract_text_from_bytes(file_bytes: bytes, mime_type: str) -> str:
    """Extract plain text from a file given its bytes and MIME type."""
    if "pdf" in mime_type:
        return extract_text_from_pdf(file_bytes)
    if "word" in mime_type or "docx" in mime_type or "openxmlformats" in mime_type:
        return extract_text_from_docx(file_bytes)
    # fallback: try PDF then DOCX
    text = extract_text_from_pdf(file_bytes)
    if not text.strip():
        text = extract_text_from_docx(file_bytes)
    return text
