import logging
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_admin
from app.core.database import get_db
from app.modules.skill_layer import service as svc
from app.modules.skill_layer.schemas import (
    ApproveOut, ChatMessageIn, ChatReplyOut, ChatStartIn, ChatStartOut,
    SkillDetailOut, SkillListItem,
)
from app.modules.users.models import User

logger = logging.getLogger(__name__)
router = APIRouter(tags=["skill_layer"])


@router.get("/admin/skills", response_model=list[SkillListItem])
async def list_skills(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return await svc.list_skills(db)


@router.get("/admin/skills/{agent_type}", response_model=SkillDetailOut)
async def get_skill_detail(
    agent_type: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return await svc.get_skill_detail(db, agent_type)


@router.post("/admin/skills/chat/start", response_model=ChatStartOut, status_code=201)
async def start_chat(
    body: ChatStartIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return await svc.start_chat(db, body.agent_type, current_user.id)


@router.post("/admin/skills/chat/{chat_id}/message", response_model=ChatReplyOut)
async def post_message(
    chat_id: uuid.UUID,
    body: ChatMessageIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return await svc.post_message(db, chat_id, body.message)


@router.post("/admin/skills/chat/{chat_id}/approve", response_model=ApproveOut)
async def approve_chat(
    chat_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return await svc.approve_chat(db, chat_id, current_user.id)


@router.post("/admin/skills/chat/{chat_id}/discard", status_code=204)
async def discard_chat(
    chat_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    await svc.discard_chat(db, chat_id)
