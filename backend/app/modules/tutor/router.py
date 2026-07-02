import json
import logging
import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_student
from app.core.database import get_db
from app.core.exceptions import AppException
from app.core.ratelimit import AI_CHAT_LIMIT, limiter
from app.modules.tutor import service as svc
from app.modules.tutor.schemas import TutorAskRequest, TutorAskResponse, TutorChatMessageOut
from app.modules.users.models import User

logger = logging.getLogger(__name__)
router = APIRouter(tags=["tutor"])


async def _resolve_tutor_session(db, current_user, body):
    """Resume an existing session's exam, or start a new one in the requested exam.
    Shared by the JSON and streaming ask endpoints."""
    from app.modules.exams.service import ensure_enrolled
    if body.chat_session_id:
        # Resume ONLY a session this student owns. The old create-on-miss fallback let a
        # forged session_id + an unenrolled exam_id slip past the enrollment gate below.
        session = await svc.get_owned_session(db, current_user.id, body.chat_session_id)
        if not session:
            raise AppException(404, "session_not_found", "Chat session not found.")
        return session, session.exam_id
    if not body.exam_id:
        raise AppException(422, "exam_required", "exam_id is required to start a new tutor chat.")
    await ensure_enrolled(db, student_id=current_user.id, exam_id=body.exam_id)
    session = await svc.get_or_create_session(db, current_user.id, body.exam_id, None)
    return session, body.exam_id


@router.post("/student/tutor/ask", response_model=TutorAskResponse)
@limiter.limit(AI_CHAT_LIMIT)
async def ask_tutor(
    request: Request,
    body: TutorAskRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_student),
):
    """Main AI tutor over an exam's chapters: topic selector → notes/book retrieval
    → main tutor. Synchronous (a fast multi-agent chat), not a tracked job."""
    question = (body.question or "").strip()
    if not question:
        raise AppException(422, "empty_question", "Question cannot be empty.")

    session, exam_id = await _resolve_tutor_session(db, current_user, body)
    result = await svc.run_tutor_chain(
        db, student_id=current_user.id, exam_id=exam_id, question=question, session=session,
    )
    return TutorAskResponse(**result)


@router.post("/student/tutor/ask/stream")
@limiter.limit(AI_CHAT_LIMIT)
async def ask_tutor_stream(
    request: Request,
    body: TutorAskRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_student),
):
    """Streaming variant of /student/tutor/ask. Returns NDJSON: a `meta` event, then
    `delta` events as the answer streams, then a `done` event with follow-ups."""
    question = (body.question or "").strip()
    if not question:
        raise AppException(422, "empty_question", "Question cannot be empty.")

    session, exam_id = await _resolve_tutor_session(db, current_user, body)

    async def event_stream():
        try:
            async for event in svc.run_tutor_chain_stream(
                db, student_id=current_user.id, exam_id=exam_id, question=question, session=session,
            ):
                yield json.dumps(event, ensure_ascii=False) + "\n"
        except Exception as exc:  # noqa: BLE001 — surface as a stream error, never a half response
            logger.exception("tutor stream failed: %s", exc)
            yield json.dumps({"type": "error", "message": "उत्तर ल्याउन सकिएन।"}, ensure_ascii=False) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


@router.get("/student/tutor/history", response_model=list[TutorChatMessageOut])
async def tutor_history(
    session_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_student),
):
    msgs = await svc.get_history(db, current_user.id, session_id)
    return [TutorChatMessageOut.model_validate(m) for m in msgs]
