"""Celery task for the Video Tutor processing pipeline (CLAUDE.md §13).

One orchestrated job, `process_video`, runs the timeline-first pipeline:
  extract audio (FFmpeg) → chunk audio → transcribe chunks → merge transcript →
  clean transcript → generate timeline → map segments to syllabus → generate full
  lecture summary → (optional) slide labels → mark ready.

The full lecture summary + timeline it produces are what the synchronous Q&A chain
(`app/modules/video/service.run_qa_chain`) later relies on. Student Q&A is NOT a job.
"""
import io
import logging
import uuid

from workers.celery_app import celery_app
from workers.runtime import run_task

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="workers.tasks.video_tasks.process_video",
    max_retries=1,
    default_retry_delay=30,
)
def process_video(self, job_id: str, video_id: str) -> None:
    async def work(db) -> None:
        from sqlalchemy import select
        from app.integrations.r2_client import get_r2
        from app.modules.files.models import File
        from app.modules.jobs.models import JobStatus
        from app.modules.jobs.service import update_job
        from app.modules.video import service as svc
        from app.modules.video.models import (
            Video, VideoAudioChunk, VideoSlideLabel, VideoSummary,
            VideoSupportSlide, VideoTimelineSegment, VideoTranscript,
        )
        from app.ai.model_router import get_provider
        from app.processing import audio_tools

        jid = uuid.UUID(job_id)
        vid = uuid.UUID(video_id)

        v_r = await db.execute(select(Video).where(Video.id == vid))
        video = v_r.scalar_one_or_none()
        if not video:
            raise ValueError(f"Video {video_id} not found")

        f_r = await db.execute(select(File).where(File.id == video.file_id))
        media = f_r.scalar_one_or_none()
        if not media:
            raise ValueError("Lecture media file not found")

        # Clear any partial output from a previous run (replace, not append).
        await _delete_children(db, vid)

        # 1) Extract audio --------------------------------------------------------
        video.processing_status = "extracting_audio"
        await update_job(db, jid, status=JobStatus.processing, progress=8, step="Extracting audio")
        await db.commit()

        media_bytes = await _to_thread(get_r2().download_fileobj, media.r2_key)
        audio_bytes, audio_mime, duration = await _to_thread(
            audio_tools.extract_audio, media_bytes, media.original_filename or "input"
        )
        if duration:
            video.duration_seconds = duration

        # Persist the extracted audio for audit / reuse.
        audio_key = f"audio/{video.id}/lecture.mp3"
        get_r2().upload_fileobj(audio_key, io.BytesIO(audio_bytes), audio_mime)
        audio_file = File(
            original_filename="lecture.mp3", display_name=f"{video.display_name} — Audio",
            mime_type=audio_mime, file_size=len(audio_bytes), r2_key=audio_key,
            uploaded_by=video.created_by,
        )
        db.add(audio_file)
        await db.flush()
        video.audio_file_id = audio_file.id
        await db.commit()

        # 2) Chunk audio ----------------------------------------------------------
        video.processing_status = "chunking_audio"
        await update_job(db, jid, progress=18, step="Chunking audio")
        await db.commit()
        chunks = await _to_thread(audio_tools.chunk_audio, audio_bytes)

        chunk_rows: list[VideoAudioChunk] = []
        for ch in chunks:
            row = VideoAudioChunk(
                video_id=video.id, chunk_index=ch.index,
                start_seconds=ch.start_seconds, end_seconds=ch.end_seconds, status="pending",
            )
            db.add(row)
            chunk_rows.append(row)
        await db.flush()

        # 3) Transcribe chunks ----------------------------------------------------
        video.processing_status = "transcribing"
        await db.commit()
        provider = get_provider("reasoning")
        dur = float(video.duration_seconds or 0)
        # Per-chunk transcripts carry their GLOBAL time window so the timeline agent
        # can anchor segment timestamps to real time (accurate even on long lectures).
        timed_raw: list[dict] = []
        total = len(chunks)
        for i, ch in enumerate(chunks):
            await update_job(
                db, jid, progress=20 + int(35 * i / max(1, total)),
                step=f"Transcribing chunk {i + 1}/{total}",
            )
            row = chunk_rows[i]
            try:
                res = await provider.transcribe(
                    ch.audio_bytes, ch.mime_type,
                    audit_ctx={"db": db, "agent_type": "VideoTranscriptionAgent",
                               "task_type": "video_transcription", "entity_type": "video",
                               "entity_id": video.id},
                )
            except Exception as exc:
                row.status = "failed"
                row.error_message = str(exc)[:480]
                raise
            text = (res.get("text") or "").strip()
            row.raw_transcript = text
            row.status = "transcribed"
            row.model_used = "gpt-4o-transcribe"
            if text:
                start = float(ch.start_seconds or 0)
                end = float(ch.end_seconds or 0)
                if end <= start:  # chunk-probe failed → fall back to the lecture duration
                    end = dur if dur > start else start
                timed_raw.append({"start_seconds": start, "end_seconds": end, "text": text})
        await db.commit()

        # 4) Merge transcript -----------------------------------------------------
        video.processing_status = "merging_transcript"
        await update_job(db, jid, progress=58, step="Merging transcript")
        raw_merged = "\n".join(c["text"] for c in timed_raw).strip()
        if not raw_merged:
            raise ValueError("Transcription produced no text")
        transcript = VideoTranscript(
            video_id=video.id, raw_merged_transcript=raw_merged, segments=None,
        )
        db.add(transcript)
        await db.commit()

        # 5) Clean transcript (per chunk, preserving time windows) ----------------
        video.processing_status = "cleaning_transcript"
        await update_job(db, jid, progress=62, step="Cleaning transcript")
        await db.commit()
        from app.ai.agents.video_transcript_cleaner_agent import VideoTranscriptCleanerAgent
        cleaner = VideoTranscriptCleanerAgent(db)
        timed_clean: list[dict] = []
        languages: list[str] = []
        for c in timed_raw:
            cleaned_res = await cleaner.clean(
                raw_transcript=c["text"], custom_instruction=video.custom_instruction, video_id=video.id,
            )
            ct = (cleaned_res.get("cleaned_transcript") or c["text"]).strip()
            timed_clean.append({"start_seconds": c["start_seconds"], "end_seconds": c["end_seconds"], "text": ct})
            if cleaned_res.get("language"):
                languages.append(cleaned_res["language"])
        cleaned = "\n\n".join(c["text"] for c in timed_clean).strip()
        transcript.cleaned_transcript = cleaned
        transcript.language = _pick_language(languages)
        transcript.model_used_for_cleaning = "gpt-5"
        await db.commit()

        # 6) Generate timeline ----------------------------------------------------
        video.processing_status = "generating_timeline"
        await update_job(db, jid, progress=70, step="Generating timeline")
        await db.commit()
        from app.ai.agents.video_timeline_agent import VideoTimelineAgent
        segments = await VideoTimelineAgent(db).generate(
            chunks=timed_clean, duration_seconds=video.duration_seconds,
            custom_instruction=video.custom_instruction, video_id=video.id,
        )
        dur = float(video.duration_seconds or 0)
        seg_rows: list[VideoTimelineSegment] = []
        for idx, seg in enumerate(segments):
            start = max(0.0, seg["start_seconds"])
            end = seg["end_seconds"] if seg["end_seconds"] > start else start
            if dur > 0:
                start = min(start, dur)
                end = min(end, dur)
            row = VideoTimelineSegment(
                video_id=video.id, segment_index=idx,
                start_seconds=start, end_seconds=end,
                label=seg["label"], description=seg["description"],
                summary=seg["summary"], original_transcript=seg["original_transcript"],
                mapping_confidence=seg.get("confidence"),
            )
            db.add(row)
            seg_rows.append(row)
        await db.flush()

        # 7) Map segments to syllabus topic/subtopic ------------------------------
        video.processing_status = "mapping_topics"
        await update_job(db, jid, progress=80, step="Mapping topics")
        tree_text, valid_topics, valid_subtopics, _valid_chapters, _topic_to_chapter = await svc.get_chapter_tree(db, video.exam_id)
        # Every segment inherits the video's chapter (the PRIMARY retrieval dimension),
        # set unconditionally so it survives even if topic mapping fails.
        for row in seg_rows:
            row.chapter = video.chapter
        if valid_topics:
            from app.ai.agents.video_segment_topic_mapper_agent import VideoSegmentTopicMapperAgent
            try:
                mappings = await VideoSegmentTopicMapperAgent(db).map_segments(
                    segments=[{"label": s.label, "description": s.description} for s in seg_rows],
                    tree_text=tree_text, video_id=video.id,
                )
                for idx, row in enumerate(seg_rows):
                    m = mappings.get(idx)
                    if not m:
                        continue
                    topic = m.get("topic")
                    row.topic = topic if topic in valid_topics else None
                    row.subtopic_ids = [s for s in m.get("subtopic_ids", []) if s in valid_subtopics]
                    row.mapping_confidence = m.get("confidence", row.mapping_confidence)
            except Exception as exc:
                logger.warning("segment topic mapping failed (continuing): %s", exc)
        await db.commit()

        # 8) Generate full lecture summary ----------------------------------------
        video.processing_status = "generating_summary"
        await update_job(db, jid, progress=86, step="Generating lecture summary")
        await db.commit()
        from app.ai.agents.video_summary_agent import VideoSummaryAgent
        summary_res = await VideoSummaryAgent(db).generate(
            cleaned_transcript=cleaned, custom_instruction=video.custom_instruction, video_id=video.id,
        )
        db.add(VideoSummary(
            video_id=video.id,
            short_summary=str(summary_res.get("short_summary") or ""),
            detailed_summary=str(summary_res.get("detailed_summary") or ""),
            key_points=summary_res.get("key_points") or [],
            exam_focused_points=summary_res.get("exam_focused_points") or [],
            important_terms=summary_res.get("important_terms") or [],
            possible_questions=summary_res.get("possible_questions") or {},
        ))
        await db.commit()

        # 9) Process slides (optional) --------------------------------------------
        slide_count = 0
        if video.support_slides_file_id:
            video.processing_status = "processing_slides"
            await update_job(db, jid, progress=92, step="Labeling slides")
            await db.commit()
            try:
                slide_count = await _process_slides(db, video, seg_rows)
            except Exception as exc:
                logger.warning("slide processing failed (continuing): %s", exc)

        # 10) Mark ready ----------------------------------------------------------
        video.processing_status = "completed"
        await db.commit()
        await update_job(
            db, jid, status=JobStatus.completed, progress=100, step="Lecture ready",
            output={
                "segments": len(seg_rows), "chunks": len(chunks),
                "slides": slide_count, "duration_seconds": video.duration_seconds,
            },
        )

    try:
        run_task(work, job_id=job_id, task=self)
    except Exception as exc:
        logger.exception("process_video failed: %s", exc)
        _mark_video_failed(video_id)
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))


# ── helpers ──────────────────────────────────────────────────────────────────────

async def _to_thread(fn, *args):
    """Run a blocking (FFmpeg) call off the event loop."""
    import asyncio
    return await asyncio.to_thread(fn, *args)


def _pick_language(languages: list[str]) -> str | None:
    """Aggregate per-chunk detected languages into one label for the transcript."""
    if not languages:
        return None
    if "nepali_english_mixed" in languages:
        return "nepali_english_mixed"
    return max(set(languages), key=languages.count)


async def _delete_children(db, video_id: uuid.UUID) -> None:
    from sqlalchemy import delete, select
    from app.modules.files.models import File
    from app.modules.video.models import (
        Video, VideoAudioChunk, VideoSlideLabel, VideoSummary, VideoTimelineSegment, VideoTranscript,
    )
    for model in (VideoTimelineSegment, VideoSummary, VideoSlideLabel, VideoAudioChunk, VideoTranscript):
        await db.execute(delete(model).where(model.video_id == video_id))

    # Drop the previously-extracted audio File so its deterministic r2_key can be
    # re-inserted on a retry (the row would otherwise collide on files.r2_key).
    video = (await db.execute(select(Video).where(Video.id == video_id))).scalar_one_or_none()
    if video and video.audio_file_id:
        video.audio_file_id = None
        await db.flush()
    await db.execute(delete(File).where(File.r2_key == f"audio/{video_id}/lecture.mp3"))
    await db.commit()


async def _process_slides(db, video, seg_rows) -> int:
    """Extract per-slide text from the support PDF and generate aligned slide labels."""
    from sqlalchemy import select
    from app.integrations.r2_client import get_r2
    from app.modules.files.models import File
    from app.modules.video import service as svc
    from app.modules.video.models import VideoSlideLabel, VideoSupportSlide
    from app.ai.agents.video_slide_label_agent import VideoSlideLabelAgent

    f_r = await db.execute(select(File).where(File.id == video.support_slides_file_id))
    slides_file = f_r.scalar_one_or_none()
    if not slides_file:
        return 0
    data = await _to_thread(get_r2().download_fileobj, slides_file.r2_key)

    # Per-page text.
    pages_text: list[str] = []
    try:
        import fitz  # PyMuPDF
        with fitz.open(stream=data, filetype="pdf") as doc:
            for page in doc:
                pages_text.append(page.get_text() or "")
    except Exception as exc:
        logger.warning("slide PDF text extraction failed: %s", exc)
        return 0
    if not pages_text:
        return 0

    slides_block = "\n\n".join(f"Slide {i + 1}:\n{t.strip()}" for i, t in enumerate(pages_text))
    timeline_block = "\n".join(
        f"{svc.fmt_timestamp(s.start_seconds)}-{svc.fmt_timestamp(s.end_seconds)} | {s.label}"
        for s in seg_rows
    )
    labels = await VideoSlideLabelAgent(db).generate(
        slides_block=slides_block, timeline_block=timeline_block, video_id=video.id,
    )

    for lab in labels:
        db.add(VideoSlideLabel(
            video_id=video.id,
            slide_number=lab["slide_number"],
            slide_id=f"slide_{lab['slide_number']:02d}",
            title=lab["title"],
            related_timestamps=lab["related_timestamps"],
            topics=lab["topics"],
            summary=lab["summary"],
        ))

    # Record the support-slides container (slide_count).
    s_r = await db.execute(select(VideoSupportSlide).where(VideoSupportSlide.video_id == video.id))
    support = s_r.scalar_one_or_none()
    if support:
        support.slide_count = len(pages_text)
    else:
        db.add(VideoSupportSlide(video_id=video.id, file_id=slides_file.id, slide_count=len(pages_text)))
    await db.commit()
    return len(labels)


def _mark_video_failed(video_id: str) -> None:
    from workers.runtime import run_async

    async def _do():
        from sqlalchemy import select
        from app.core.database import AsyncSessionLocal
        from app.modules.video.models import Video
        try:
            async with AsyncSessionLocal() as db:
                r = await db.execute(select(Video).where(Video.id == uuid.UUID(video_id)))
                v = r.scalar_one_or_none()
                if v:
                    v.processing_status = "failed"
                    await db.commit()
        except Exception:
            logger.exception("Could not mark video %s failed", video_id)

    try:
        run_async(_do())
    except Exception:
        logger.exception("mark_video_failed wrapper failed")
