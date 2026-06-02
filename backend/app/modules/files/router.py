import uuid

from fastapi import APIRouter, Depends, Query, UploadFile, File as FastAPIFile, Form
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, require_admin
from app.core.database import get_db
from app.core.exceptions import AppException
from app.integrations.r2_client import get_r2
from app.modules.files.models import File
from app.modules.files.schemas import FileOut, FileWithUrl
from app.modules.files.service import store_upload
from app.modules.users.models import User

router = APIRouter(tags=["files"])


class FileListOut(BaseModel):
    items: list[FileOut]
    total: int
    page: int
    per_page: int


@router.post("/files/upload", response_model=FileOut, status_code=201)
async def upload_file(
    file: UploadFile = FastAPIFile(...),
    context: str = Form(...),
    display_name: str = Form(""),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    name = display_name.strip() or file.filename or "upload"
    record = await store_upload(file, context=context, display_name=name, uploaded_by=current_user.id, db=db)
    return FileOut.model_validate(record)


@router.get("/files/{file_id}/url", response_model=FileWithUrl)
async def get_file_url(
    file_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(File).where(File.id == file_id))
    file_record = result.scalar_one_or_none()
    if not file_record:
        raise AppException(404, "not_found", "File not found.")

    signed_url = get_r2().get_signed_url(file_record.r2_key, expires_in=3600)
    return FileWithUrl(**FileOut.model_validate(file_record).model_dump(), signed_url=signed_url)


@router.get("/admin/files", response_model=FileListOut)
async def list_files(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    base = select(File).order_by(File.created_at.desc())
    total_result = await db.execute(select(func.count()).select_from(File))
    total = total_result.scalar_one()
    result = await db.execute(base.offset((page - 1) * per_page).limit(per_page))
    files = result.scalars().all()
    return FileListOut(
        items=[FileOut.model_validate(f) for f in files],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.delete("/admin/files/{file_id}", status_code=204)
async def delete_file(
    file_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(File).where(File.id == file_id))
    file_record = result.scalar_one_or_none()
    if not file_record:
        raise AppException(404, "not_found", "File not found.")
    try:
        get_r2().delete_object(file_record.r2_key)
    except Exception:
        pass  # proceed to delete DB record even if R2 deletion fails
    await db.delete(file_record)
    await db.commit()
