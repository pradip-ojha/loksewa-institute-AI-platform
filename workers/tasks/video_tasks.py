"""Celery task for the Video Tutor processing pipeline (CLAUDE.md §13).

One orchestrated job, `process_video`, runs the timeline-first pipeline:
  extract audio (FFmpeg) → chunk audio → transcribe chunks → merge transcript →
  clean transcript → generate timeline → map segments to syllabus → generate full
  lecture summary → (optional) slide labels → mark ready.

The full lecture summary + timeline it produces are what the synchronous Q&A chain
(`app/modules/video/service.run_qa_chain`) later relies on. Student Q&A is NOT a job.
"""
import asyncio
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
    # Borrow-per-use session model (run_task manage_session=False): this pipeline runs
    # for many minutes (transcription, cleaning, timeline/summary generation). A single
    # session held across it would sit idle through each AI phase and be dropped
    # server-side, crashing the next commit. Instead `work()` takes NO long-lived session
    # — every DB touch opens a short-lived session and returns the connection to the pool,
    # so nothing is ever held idle across an AI/render phase (LOAD → WORK → SAVE), matching
    # check_answer_sheet / generate_test_skills.
    async def work() -> None:
        from sqlalchemy import select
        from app.core.database import AsyncSessionLocal
        from app.integrations.r2_client import get_r2
        from app.modules.files.models import File
        from app.modules.jobs.models import JobStatus
        from app.modules.jobs.service import update_job
        from app.modules.video import service as svc
        from app.modules.video.models import (
            Video, VideoAudioChunk, VideoSummary, VideoTimelineSegment, VideoTranscript,
        )
        from app.ai.model_router import get_provider
        from app.processing import audio_tools

        jid = uuid.UUID(job_id)
        vid = uuid.UUID(video_id)

        # ── tiny borrow-per-use helpers (each opens + closes its own session) ────
        async def _job(**kw) -> None:
            async with AsyncSessionLocal() as db:
                await update_job(db, jid, **kw)

        async def _phase(status: str | None = None, **job_kw) -> None:
            """Advance the video's processing_status (and optionally the job) in one
            short session."""
            async with AsyncSessionLocal() as db:
                if status:
                    v = (await db.execute(select(Video).where(Video.id == vid))).scalar_one_or_none()
                    if v:
                        v.processing_status = status
                if job_kw:
                    await update_job(db, jid, **job_kw)
                await db.commit()

        # ── LOAD: snapshot the video + media scalars, clear prior children ───────
        async with AsyncSessionLocal() as db:
            video = (await db.execute(select(Video).where(Video.id == vid))).scalar_one_or_none()
            if not video:
                raise ValueError(f"Video {video_id} not found")
            media = (await db.execute(select(File).where(File.id == video.file_id))).scalar_one_or_none()
            if not media:
                raise ValueError("Lecture media file not found")
            custom_instruction = video.custom_instruction
            exam_id = video.exam_id
            chapter = video.chapter
            created_by = video.created_by
            display_name = video.display_name
            support_slides_file_id = video.support_slides_file_id
            media_r2_key = media.r2_key
            media_filename = media.original_filename or "input"
            # Clear any partial output from a previous run (replace, not append).
            await _delete_children(db, vid)
            await update_job(db, jid, status=JobStatus.processing, progress=8, step="Extracting audio")

        # 1) Extract audio (FFmpeg off-loop, no DB held) --------------------------
        await _phase("extracting_audio")
        media_bytes = await _to_thread(get_r2().download_fileobj, media_r2_key)
        audio_bytes, audio_mime, duration = await _to_thread(
            audio_tools.extract_audio, media_bytes, media_filename
        )
        duration = float(duration or 0)

        # SAVE: persist the extracted audio for audit / reuse + duration ----------
        audio_key = f"audio/{vid}/lecture.mp3"
        get_r2().upload_fileobj(audio_key, io.BytesIO(audio_bytes), audio_mime)
        async with AsyncSessionLocal() as db:
            audio_file = File(
                original_filename="lecture.mp3", display_name=f"{display_name} — Audio",
                mime_type=audio_mime, file_size=len(audio_bytes), r2_key=audio_key,
                uploaded_by=created_by,
            )
            db.add(audio_file)
            await db.flush()
            v = (await db.execute(select(Video).where(Video.id == vid))).scalar_one()
            v.audio_file_id = audio_file.id
            if duration:
                v.duration_seconds = duration
            await db.commit()

        # 2) Chunk audio (CPU, no DB held) ----------------------------------------
        await _phase("chunking_audio", progress=18, step="Chunking audio")
        chunks = await _to_thread(audio_tools.chunk_audio, audio_bytes)

        # SAVE: persist one chunk row per audio chunk ----------------------------
        async with AsyncSessionLocal() as db:
            for ch in chunks:
                db.add(VideoAudioChunk(
                    video_id=vid, chunk_index=ch.index,
                    start_seconds=ch.start_seconds, end_seconds=ch.end_seconds, status="pending",
                ))
            await db.commit()

        # 3) Transcribe chunks (PARALLEL, each on its OWN short-lived session) ----
        # Chunks are independent (each carries its own global time window), so they
        # transcribe concurrently. Each call borrows its OWN short-lived session for
        # audit logging (sharing one AsyncSession across concurrent calls is unsafe).
        total = len(chunks)
        await _phase("transcribing", progress=22, step=f"Transcribing {total} chunks")
        provider = get_provider("reasoning")

        t_sem = asyncio.Semaphore(6)

        async def _transcribe_one(idx: int, ch):
            async with t_sem:
                async with AsyncSessionLocal() as tdb:
                    res = await provider.transcribe(
                        ch.audio_bytes, ch.mime_type,
                        audit_ctx={"db": tdb, "agent_type": "VideoTranscriptionAgent",
                                   "task_type": "video_transcription", "entity_type": "video",
                                   "entity_id": vid},
                    )
            return idx, (res.get("text") or "").strip()

        results = await asyncio.gather(
            *[_transcribe_one(i, ch) for i, ch in enumerate(chunks)],
            return_exceptions=True,
        )

        # Reassemble in chunk order. Per-chunk transcripts carry their GLOBAL time
        # window so the timeline agent can anchor segment timestamps to real time.
        # Partial success: one bad chunk doesn't kill the lecture, but if EVERY chunk
        # failed we surface the error so the job fails honestly.
        timed_raw: list[dict] = []
        failures = 0
        chunk_updates: list[dict] = []   # applied to the chunk rows in one SAVE burst
        for i, ch in enumerate(chunks):
            res = results[i]
            if isinstance(res, Exception):
                failures += 1
                chunk_updates.append({"chunk_index": ch.index, "status": "failed",
                                      "error_message": str(res)[:480]})
                logger.warning("transcription failed for chunk %d (continuing): %s", i, res)
                continue
            _, text = res
            chunk_updates.append({"chunk_index": ch.index, "status": "transcribed",
                                  "raw_transcript": text, "model_used": "gpt-4o-transcribe"})
            if text:
                start = float(ch.start_seconds or 0)
                end = float(ch.end_seconds or 0)
                if end <= start:  # chunk-probe failed → fall back to the lecture duration
                    end = duration if duration > start else start
                timed_raw.append({"start_seconds": start, "end_seconds": end, "text": text})
        if failures == total:
            raise RuntimeError("All transcription chunks failed")

        # SAVE: write transcripts / status back onto the chunk rows --------------
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(VideoAudioChunk).where(VideoAudioChunk.video_id == vid)
            )).scalars().all()
            by_idx = {r.chunk_index: r for r in rows}
            for u in chunk_updates:
                r = by_idx.get(u["chunk_index"])
                if not r:
                    continue
                r.status = u["status"]
                if "raw_transcript" in u:
                    r.raw_transcript = u["raw_transcript"]
                if "model_used" in u:
                    r.model_used = u["model_used"]
                if "error_message" in u:
                    r.error_message = u["error_message"]
            await db.commit()

        # 4) Merge transcript + persist -------------------------------------------
        await _phase("merging_transcript", progress=58, step="Merging transcript")
        raw_merged = "\n".join(c["text"] for c in timed_raw).strip()
        if not raw_merged:
            raise ValueError("Transcription produced no text")
        async with AsyncSessionLocal() as db:
            db.add(VideoTranscript(video_id=vid, raw_merged_transcript=raw_merged, segments=None))
            await db.commit()

        # 5) Clean transcript (PARALLEL per chunk, each on its OWN session) -------
        # Chunks clean independently and concurrently, each on its OWN short-lived
        # session for audit. Reassembled in order so transcript flow is preserved; a
        # failed chunk falls back to its raw text (best-effort, never fails the job).
        await _phase("cleaning_transcript", progress=62, step="Cleaning transcript")
        from app.ai.agents.video_transcript_cleaner_agent import VideoTranscriptCleanerAgent

        c_sem = asyncio.Semaphore(6)

        async def _clean_one(idx: int, c: dict):
            async with c_sem:
                async with AsyncSessionLocal() as cdb:
                    cleaned_res = await VideoTranscriptCleanerAgent(cdb).clean(
                        raw_transcript=c["text"], custom_instruction=custom_instruction,
                        video_id=vid,
                    )
            return idx, (cleaned_res.get("cleaned_transcript") or c["text"]).strip(), cleaned_res.get("language")

        clean_results = await asyncio.gather(
            *[_clean_one(i, c) for i, c in enumerate(timed_raw)],
            return_exceptions=True,
        )

        timed_clean: list[dict] = []
        languages: list[str] = []
        for i, c in enumerate(timed_raw):
            res = clean_results[i]
            if isinstance(res, Exception):
                logger.warning("transcript cleaning failed for chunk %d (using raw): %s", i, res)
                timed_clean.append({"start_seconds": c["start_seconds"], "end_seconds": c["end_seconds"], "text": c["text"]})
                continue
            _, ct, lang = res
            timed_clean.append({"start_seconds": c["start_seconds"], "end_seconds": c["end_seconds"], "text": ct})
            if lang:
                languages.append(lang)
        cleaned = "\n\n".join(c["text"] for c in timed_clean).strip()

        # SAVE: cleaned transcript onto the row ----------------------------------
        async with AsyncSessionLocal() as db:
            tr = (await db.execute(
                select(VideoTranscript).where(VideoTranscript.video_id == vid)
            )).scalar_one_or_none()
            if tr:
                tr.cleaned_transcript = cleaned
                tr.language = _pick_language(languages)
                tr.model_used_for_cleaning = "gpt-5"
                await db.commit()

        # 6) Generate timeline (AI in its own session) ----------------------------
        await _phase("generating_timeline", progress=70, step="Generating timeline")
        from app.ai.agents.video_timeline_agent import VideoTimelineAgent
        async with AsyncSessionLocal() as db:
            segments = await VideoTimelineAgent(db).generate(
                chunks=timed_clean, duration_seconds=duration,
                custom_instruction=custom_instruction, video_id=vid,
            )

        # SAVE: persist segments (chapter inherited unconditionally), snapshot the
        # label/description each mapping call needs ------------------------------
        seg_meta: list[dict] = []
        async with AsyncSessionLocal() as db:
            for idx, seg in enumerate(segments):
                start = max(0.0, seg["start_seconds"])
                end = seg["end_seconds"] if seg["end_seconds"] > start else start
                if duration > 0:
                    start = min(start, duration)
                    end = min(end, duration)
                db.add(VideoTimelineSegment(
                    video_id=vid, segment_index=idx,
                    start_seconds=start, end_seconds=end,
                    label=seg["label"], description=seg["description"],
                    summary=seg["summary"], original_transcript=seg["original_transcript"],
                    # Inherit the video's chapter (PRIMARY retrieval dimension) up front so
                    # it survives even if topic mapping below fails.
                    chapter=chapter,
                    mapping_confidence=seg.get("confidence"),
                ))
                seg_meta.append({"idx": idx, "label": seg["label"], "description": seg["description"]})
            await db.commit()

        # 7) Map segments to syllabus topic/subtopic ------------------------------
        await _phase("mapping_topics", progress=80, step="Mapping topics")
        async with AsyncSessionLocal() as db:
            tree_text, valid_topics, valid_subtopics, _vc, _ttc = await svc.get_chapter_tree(db, exam_id)
        if valid_topics and seg_meta:
            from app.ai.agents.video_segment_topic_mapper_agent import VideoSegmentTopicMapperAgent
            try:
                async with AsyncSessionLocal() as db:
                    mappings = await VideoSegmentTopicMapperAgent(db).map_segments(
                        segments=[{"label": s["label"], "description": s["description"]} for s in seg_meta],
                        tree_text=tree_text, video_id=vid,
                    )
                async with AsyncSessionLocal() as db:
                    seg_rows = (await db.execute(
                        select(VideoTimelineSegment).where(VideoTimelineSegment.video_id == vid)
                    )).scalars().all()
                    by_seg_idx = {r.segment_index: r for r in seg_rows}
                    for s in seg_meta:
                        m = mappings.get(s["idx"])
                        row = by_seg_idx.get(s["idx"])
                        if not m or not row:
                            continue
                        topic = m.get("topic")
                        row.topic = topic if topic in valid_topics else None
                        row.subtopic_ids = [x for x in m.get("subtopic_ids", []) if x in valid_subtopics]
                        row.mapping_confidence = m.get("confidence", row.mapping_confidence)
                    await db.commit()
            except Exception as exc:
                logger.warning("segment topic mapping failed (continuing): %s", exc)

        # 8) Generate full lecture summary (AI in its own session) + persist ------
        await _phase("generating_summary", progress=86, step="Generating lecture summary")
        from app.ai.agents.video_summary_agent import VideoSummaryAgent
        async with AsyncSessionLocal() as db:
            summary_res = await VideoSummaryAgent(db).generate(
                cleaned_transcript=cleaned, custom_instruction=custom_instruction, video_id=vid,
            )
        async with AsyncSessionLocal() as db:
            db.add(VideoSummary(
                video_id=vid,
                short_summary=str(summary_res.get("short_summary") or ""),
                detailed_summary=str(summary_res.get("detailed_summary") or ""),
                key_points=summary_res.get("key_points") or [],
                exam_focused_points=summary_res.get("exam_focused_points") or [],
                important_terms=summary_res.get("important_terms") or [],
                possible_questions=summary_res.get("possible_questions") or {},
            ))
            await db.commit()

        # 9) Process slides (optional, borrow-per-use inside _process_slides) -----
        slide_count = 0
        if support_slides_file_id:
            await _phase("processing_slides", progress=92, step="Labeling slides")
            try:
                slide_count = await _process_slides(vid, support_slides_file_id)
            except Exception as exc:
                logger.warning("slide processing failed (continuing): %s", exc)

        # 10) Mark ready ----------------------------------------------------------
        await _phase("completed")
        await _job(status=JobStatus.completed, progress=100, step="Lecture ready",
                   output={"segments": len(seg_meta), "chunks": len(chunks),
                           "slides": slide_count, "duration_seconds": duration})

    try:
        run_task(work, job_id=job_id, task=self, manage_session=False)
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


async def _process_slides(video_id: uuid.UUID, support_slides_file_id: uuid.UUID) -> int:
    """Extract per-slide text from the support PDF and generate aligned slide labels.

    Borrow-per-use: loads the slides file + timeline block in one short session, runs
    the label AI call in its own session, persists labels in a final session — no
    pooled connection is ever held idle across the AI call."""
    from sqlalchemy import select
    from app.core.database import AsyncSessionLocal
    from app.integrations.r2_client import get_r2
    from app.modules.files.models import File
    from app.modules.video import service as svc
    from app.modules.video.models import VideoSlideLabel, VideoSupportSlide, VideoTimelineSegment
    from app.ai.agents.video_slide_label_agent import VideoSlideLabelAgent

    # LOAD: slides file + the timeline block the label agent aligns against ──────
    async with AsyncSessionLocal() as db:
        slides_file = (await db.execute(
            select(File).where(File.id == support_slides_file_id)
        )).scalar_one_or_none()
        if not slides_file:
            return 0
        slides_r2_key = slides_file.r2_key
        slides_file_id = slides_file.id
        seg_rows = (await db.execute(
            select(VideoTimelineSegment).where(VideoTimelineSegment.video_id == video_id)
            .order_by(VideoTimelineSegment.segment_index.asc())
        )).scalars().all()
        timeline_block = "\n".join(
            f"{svc.fmt_timestamp(s.start_seconds)}-{svc.fmt_timestamp(s.end_seconds)} | {s.label}"
            for s in seg_rows
        )

    data = await _to_thread(get_r2().download_fileobj, slides_r2_key)

    # Per-page text (CPU, no DB held).
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

    # AI call in its own short session (audit logging) ──────────────────────────
    async with AsyncSessionLocal() as db:
        labels = await VideoSlideLabelAgent(db).generate(
            slides_block=slides_block, timeline_block=timeline_block, video_id=video_id,
        )

    # SAVE: persist labels + the support-slides container (slide_count) ──────────
    async with AsyncSessionLocal() as db:
        for lab in labels:
            db.add(VideoSlideLabel(
                video_id=video_id,
                slide_number=lab["slide_number"],
                slide_id=f"slide_{lab['slide_number']:02d}",
                title=lab["title"],
                related_timestamps=lab["related_timestamps"],
                topics=lab["topics"],
                summary=lab["summary"],
            ))
        support = (await db.execute(
            select(VideoSupportSlide).where(VideoSupportSlide.video_id == video_id)
        )).scalar_one_or_none()
        if support:
            support.slide_count = len(pages_text)
        else:
            db.add(VideoSupportSlide(video_id=video_id, file_id=slides_file_id, slide_count=len(pages_text)))
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
