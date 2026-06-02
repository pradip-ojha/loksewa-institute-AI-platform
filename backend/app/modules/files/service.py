import io
import uuid

import magic
from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.integrations.r2_client import get_r2
from app.modules.files.models import File

# Allowlist: context → (allowed mime types, max bytes)
FILE_RULES: dict[str, tuple[set[str], int]] = {
    "document": (
        {"application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/msword"},
        50 * 1024 * 1024,
    ),
    "mcq-documents": (
        {"application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/msword"},
        50 * 1024 * 1024,
    ),
    "subjective-tests": (
        {"application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/msword"},
        50 * 1024 * 1024,
    ),
    "answer-sheets": (
        {"application/pdf", "image/jpeg", "image/png", "image/webp"},
        20 * 1024 * 1024,
    ),
    "videos": (
        {"video/mp4", "video/quicktime", "video/webm", "video/x-msvideo", "audio/mpeg", "audio/wav", "audio/x-wav", "audio/mp4", "audio/ogg"},
        2 * 1024 * 1024 * 1024,
    ),
    "lecture-slides": (
        {"application/pdf"},
        50 * 1024 * 1024,
    ),
    "answer_sheet": (
        {"application/pdf", "image/jpeg", "image/png", "image/webp"},
        20 * 1024 * 1024,
    ),
    "video": (
        {"video/mp4", "video/quicktime", "video/webm", "video/x-msvideo"},
        2 * 1024 * 1024 * 1024,
    ),
    "audio": (
        {"audio/mpeg", "audio/wav", "audio/x-wav", "audio/mp4", "audio/ogg"},
        500 * 1024 * 1024,
    ),
    "slides": (
        {"application/pdf"},
        50 * 1024 * 1024,
    ),
}


async def store_upload(
    upload: UploadFile,
    *,
    context: str,
    display_name: str,
    uploaded_by: uuid.UUID,
    db: AsyncSession,
) -> File:
    allowed_mimes, max_size = FILE_RULES.get(context, (set(), 0))

    raw = await upload.read()
    if not raw:
        raise AppException(400, "empty_file", "Uploaded file is empty.")

    # validate size
    if len(raw) > max_size:
        mb = max_size // (1024 * 1024)
        raise AppException(413, "file_too_large", f"File exceeds {mb} MB limit for {context}.")

    # validate type via magic bytes
    detected_mime = magic.from_buffer(raw[:2048], mime=True)
    if detected_mime not in allowed_mimes:
        raise AppException(
            415,
            "unsupported_file_type",
            f"File type '{detected_mime}' is not allowed for {context}. Allowed: {', '.join(sorted(allowed_mimes))}",
        )

    r2_key = f"{context}/{uuid.uuid4()}/{upload.filename}"
    get_r2().upload_fileobj(r2_key, io.BytesIO(raw), detected_mime)

    file_record = File(
        original_filename=upload.filename or "upload",
        display_name=display_name,
        mime_type=detected_mime,
        file_size=len(raw),
        r2_key=r2_key,
        uploaded_by=uploaded_by,
    )
    db.add(file_record)
    await db.commit()
    await db.refresh(file_record)
    return file_record
