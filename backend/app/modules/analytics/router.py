import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_admin
from app.core.database import get_db
from app.modules.analytics import service as svc
from app.modules.users.models import User

router = APIRouter(tags=["analytics"], prefix="/admin/analytics")


@router.get("/mcq/overview")
async def mcq_overview(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    return await svc.mcq_overview(db)


@router.get("/subjective/overview")
async def subjective_overview(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    return await svc.subjective_overview(db)


@router.get("/subjective/tests/{test_id}")
async def subjective_test_detail(
    test_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    return await svc.subjective_test_detail(db, test_id)


@router.get("/video")
async def video_overview(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    return await svc.video_overview(db)


@router.get("/video/{video_id}")
async def video_detail(
    video_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    return await svc.video_detail(db, video_id)
