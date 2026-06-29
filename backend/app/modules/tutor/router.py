import logging
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_student
from app.core.database import get_db
from app.core.exceptions import AppException
from app.modules.tutor import service as svc
from app.modules.tutor.schemas import TutorAskRequest, TutorAskResponse, TutorChatMessageOut
from app.modules.users.models import User

logger = logging.getLogger(__name__)
router = APIRouter(tags=["tutor"])


@router.post("/student/tutor/ask", response_model=TutorAskResponse)
async def ask_tutor(
    body: TutorAskRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_student),
):
    """Main AI tutor over an exam's chapters: topic selector → notes/book retrieval
    → main tutor. Synchronous (a fast multi-agent chat), not a tracked job."""
    question = (body.question or "").strip()
    if not question:
        raise AppException(422, "empty_question", "Question cannot be empty.")

    from app.modules.exams.service import ensure_enrolled
    # Resume an existing session's exam, or start a new one in the requested exam.
    if body.chat_session_id:
        session = await svc.get_or_create_session(db, current_user.id, body.exam_id or uuid.uuid4(), body.chat_session_id)
        exam_id = session.exam_id
    else:
        if not body.exam_id:
            raise AppException(422, "exam_required", "exam_id is required to start a new tutor chat.")
        await ensure_enrolled(db, student_id=current_user.id, exam_id=body.exam_id)
        session = await svc.get_or_create_session(db, current_user.id, body.exam_id, None)
        exam_id = body.exam_id

    result = await svc.run_tutor_chain(
        db, student_id=current_user.id, exam_id=exam_id, question=question, session=session,
    )
    return TutorAskResponse(**result)


@router.get("/student/tutor/history", response_model=list[TutorChatMessageOut])
async def tutor_history(
    session_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_student),
):
    msgs = await svc.get_history(db, current_user.id, session_id)
    return [TutorChatMessageOut.model_validate(m) for m in msgs]
