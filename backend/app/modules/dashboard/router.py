from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_admin
from app.core.database import get_db
from app.modules.dashboard import service as svc
from app.modules.users.models import User

router = APIRouter(tags=["dashboard"])


class ActivityItem(BaseModel):
    type: str
    title: str
    status: str
    created_at: datetime


class DashboardStats(BaseModel):
    total_students: int
    total_active_students: int
    total_knowledge_documents: int
    total_approved_mcqs: int
    total_active_mcq_sets: int
    total_subjective_tests: int
    total_videos: int
    pending_jobs: int
    failed_jobs: int
    recent_activity: list[ActivityItem]


@router.get("/admin/dashboard/stats", response_model=DashboardStats)
async def dashboard_stats(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    return await svc.get_stats(db)
