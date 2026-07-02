import json
import logging
import uuid

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_admin, require_student
from app.core.database import get_db
from app.core.exceptions import AppException
from app.core.ratelimit import AI_CHAT_LIMIT, limiter
from app.modules.files.service import store_upload
from app.modules.jobs.models import JobStatus, ProcessingJob
from app.modules.jobs.schemas import JobOut
from app.modules.jobs.service import create_job, update_job
from app.modules.users.models import User
from app.modules.video import service as svc
from app.modules.video.models import Video
from app.modules.video.schemas import (
    AskRequest, AskResponse, ChatMessageOut, StudentPlayerData, StudentVideoListItem,
    VideoDetailOut, VideoListOut, VideoOut,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["video"])


async def _job_out(db: AsyncSession, job_id: uuid.UUID) -> JobOut:
    r = await db.execute(select(ProcessingJob).where(ProcessingJob.id == job_id))
    return JobOut.model_validate(r.scalar_one())


async def _dispatch_processing(db: AsyncSession, video: Video, created_by: uuid.UUID) -> ProcessingJob:
    job = await create_job(
        db, job_type="video_processing", created_by=created_by,
        input_reference={"video_id": str(video.id)},
    )
    video.processing_job_id = job.id
    video.processing_status = "uploaded"
    await db.commit()

    from app.core.celery_client import get_celery
    task = get_celery().send_task(
        "workers.tasks.video_tasks.process_video",
        args=[str(job.id), str(video.id)],
        queue="kvi_ai_video",
    )
    await update_job(db, job.id, status=JobStatus.processing, celery_task_id=task.id)
    return job


# ── Admin ────────────────────────────────────────────────────────────────────────

@router.post("/admin/videos", response_model=JobOut, status_code=201)
async def create_video(
    title: str = Form(...),
    exam_id: uuid.UUID = Form(...),
    chapter: str | None = Form(None),
    topic: str | None = Form(None),
    subtopic: str | None = Form(None),
    custom_instruction: str | None = Form(None),
    media: UploadFile = File(...),
    support_slides: UploadFile | None = File(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Upload a lecture (video or audio) + optional support-slides PDF, then run the
    processing pipeline (audio → transcript → timeline → summary → slides)."""
    from app.modules.exams.service import get_exam_or_404
    await get_exam_or_404(db, exam_id)

    media_file = await store_upload(
        media, context="videos",
        display_name=f"{title} — Media",
        uploaded_by=current_user.id, db=db,
    )
    slides_file = (
        await store_upload(support_slides, context="lecture-slides",
                           display_name=f"{title} — Support Slides",
                           uploaded_by=current_user.id, db=db)
        if support_slides else None
    )

    video = Video(
        display_name=title,
        exam_id=exam_id,
        chapter=chapter or None,
        topic=topic or None,
        subtopic=subtopic or None,
        custom_instruction=custom_instruction or None,
        file_id=media_file.id,
        support_slides_file_id=slides_file.id if slides_file else None,
        is_audio_only=media_file.mime_type.startswith("audio/"),
        processing_status="uploaded",
        status="draft",
        created_by=current_user.id,
    )
    db.add(video)
    await db.flush()

    if slides_file:
        from app.modules.video.models import VideoSupportSlide
        db.add(VideoSupportSlide(video_id=video.id, file_id=slides_file.id))

    job = await _dispatch_processing(db, video, current_user.id)
    return await _job_out(db, job.id)


@router.get("/admin/videos", response_model=VideoListOut)
async def list_videos(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    items, total = await svc.list_videos(db, page=page, per_page=per_page)
    return VideoListOut(
        items=[VideoOut(**svc.video_out_fields(v)) for v in items],
        total=total, page=page, per_page=per_page,
    )


@router.get("/admin/videos/{video_id}", response_model=VideoDetailOut)
async def get_video(
    video_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    v = await svc.get_video(db, video_id)
    if not v:
        raise AppException(404, "not_found", "Video not found.")
    timeline = await svc.get_timeline(db, video_id)
    summary = await svc.get_summary(db, video_id)
    slides = await svc.get_slides(db, video_id)
    return VideoDetailOut(
        **svc.video_out_fields(v),
        custom_instruction=v.custom_instruction,
        media_url=await svc.signed_url(db, v.file_id),
        slides_url=await svc.signed_url(db, v.support_slides_file_id),
        summary=svc.summary_out(summary),
        timeline=[svc.timeline_out(s) for s in timeline],
        slides=[svc.slide_out(s) for s in slides],
    )


@router.post("/admin/videos/{video_id}/retry", response_model=JobOut, status_code=201)
async def retry_video(
    video_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    v = await svc.get_video(db, video_id)
    if not v:
        raise AppException(404, "not_found", "Video not found.")
    if v.processing_status == "completed":
        raise AppException(422, "already_done", "This video is already processed.")
    job = await _dispatch_processing(db, v, current_user.id)
    return await _job_out(db, job.id)


@router.post("/admin/videos/{video_id}/activate", response_model=VideoOut)
async def activate_video(
    video_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    v = await svc.get_video(db, video_id)
    if not v:
        raise AppException(404, "not_found", "Video not found.")
    if v.processing_status != "completed":
        raise AppException(422, "not_ready", "Processing has not completed yet.")
    v = await svc.set_status(db, video_id, "active")
    return VideoOut(**svc.video_out_fields(v))


@router.post("/admin/videos/{video_id}/deactivate", response_model=VideoOut)
async def deactivate_video(
    video_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    v = await svc.set_status(db, video_id, "archived")
    if not v:
        raise AppException(404, "not_found", "Video not found.")
    return VideoOut(**svc.video_out_fields(v))


@router.delete("/admin/videos/{video_id}", status_code=204)
async def delete_video(
    video_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    ok = await svc.delete_video(db, video_id)
    if not ok:
        raise AppException(404, "not_found", "Video not found.")


# ── Student ──────────────────────────────────────────────────────────────────────

@router.get("/student/videos", response_model=list[StudentVideoListItem])
async def student_list_videos(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_student),
):
    videos = await svc.list_student_videos(db, current_user.id)
    return [
        StudentVideoListItem(
            id=v.id, display_name=v.display_name, topic=v.topic, subtopic=v.subtopic,
            duration_seconds=v.duration_seconds, is_audio_only=v.is_audio_only,
        )
        for v in videos
    ]


@router.get("/student/videos/{video_id}", response_model=StudentPlayerData)
async def student_player_data(
    video_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_student),
):
    v = await svc.get_video(db, video_id)
    if not v or v.status != "active" or v.processing_status != "completed":
        raise AppException(404, "not_found", "Video is not available.")
    # Scope to the student's enrolled exams — the list endpoint already filters, but
    # direct-by-id access must too, or any student could stream any active video / run
    # its tutor across exams they aren't enrolled in.
    from app.modules.exams.service import ensure_enrolled
    await ensure_enrolled(db, student_id=current_user.id, exam_id=v.exam_id)
    timeline = await svc.get_timeline(db, video_id)
    summary = await svc.get_summary(db, video_id)
    slides = await svc.get_slides(db, video_id)
    await svc.record_view(db, video_id, current_user.id)
    return StudentPlayerData(
        id=v.id,
        display_name=v.display_name,
        media_url=await svc.signed_url(db, v.file_id),
        is_audio_only=v.is_audio_only,
        duration_seconds=v.duration_seconds,
        summary=svc.summary_out(summary),
        timeline=[svc.timeline_out(s) for s in timeline],
        slides=[svc.slide_out(s) for s in slides],
    )


@router.post("/student/videos/{video_id}/ask", response_model=AskResponse)
@limiter.limit(AI_CHAT_LIMIT)
async def ask_tutor(
    request: Request,
    video_id: uuid.UUID,
    body: AskRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_student),
):
    v = await svc.get_video(db, video_id)
    if not v or v.status != "active" or v.processing_status != "completed":
        raise AppException(404, "not_found", "Video is not available.")
    from app.modules.exams.service import ensure_enrolled
    await ensure_enrolled(db, student_id=current_user.id, exam_id=v.exam_id)
    question = (body.question or "").strip()
    if not question:
        raise AppException(422, "empty_question", "Question cannot be empty.")

    session = await svc.get_or_create_chat_session(db, video_id, current_user.id, body.chat_session_id)
    result = await svc.run_qa_chain(
        db, video=v, question=question,
        current_video_time=body.current_video_time, session=session,
    )
    return AskResponse(**result)


@router.post("/student/videos/{video_id}/ask/stream")
@limiter.limit(AI_CHAT_LIMIT)
async def ask_tutor_stream(
    request: Request,
    video_id: uuid.UUID,
    body: AskRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_student),
):
    """Streaming variant of /student/videos/{id}/ask. Returns NDJSON: a `meta` event
    (selected segments + topic), then `delta` events as the answer streams, then `done`."""
    v = await svc.get_video(db, video_id)
    if not v or v.status != "active" or v.processing_status != "completed":
        raise AppException(404, "not_found", "Video is not available.")
    from app.modules.exams.service import ensure_enrolled
    await ensure_enrolled(db, student_id=current_user.id, exam_id=v.exam_id)
    question = (body.question or "").strip()
    if not question:
        raise AppException(422, "empty_question", "Question cannot be empty.")

    session = await svc.get_or_create_chat_session(db, video_id, current_user.id, body.chat_session_id)

    async def event_stream():
        try:
            async for event in svc.run_qa_chain_stream(
                db, video=v, question=question,
                current_video_time=body.current_video_time, session=session,
            ):
                yield json.dumps(event, ensure_ascii=False) + "\n"
        except Exception as exc:  # noqa: BLE001 — surface as a stream error
            logger.exception("video tutor stream failed: %s", exc)
            yield json.dumps({"type": "error", "message": "उत्तर ल्याउन सकिएन।"}, ensure_ascii=False) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


@router.get("/student/videos/{video_id}/history", response_model=list[ChatMessageOut])
async def student_chat_history(
    video_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_student),
):
    msgs = await svc.get_chat_history(db, video_id, current_user.id)
    return [ChatMessageOut.model_validate(m) for m in msgs]
