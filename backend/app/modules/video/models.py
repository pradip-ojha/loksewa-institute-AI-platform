"""Video Tutor data model.

Timeline-first architecture (CLAUDE.md §13): a lecture (`videos` row) is processed
into a cleaned transcript, a queryable TIMELINE of meaningful teaching segments, a
full lecture summary, and (optionally) slide labels. Student Q&A is grounded in the
timeline — a segment router picks the relevant segment(s), a topic/subtopic router
picks from the live syllabus tree, and supporting knowledge chunks are fetched only
inside that filtered set. Nothing about routing relies on a global transcript vector
search.

Times are stored in SECONDS (float) so the player can seek precisely; the API/agents
format to HH:MM:SS where a human-readable timestamp is useful.
"""
import uuid
from datetime import datetime

from sqlalchemy import String, Integer, Float, DateTime, ForeignKey, Text, Boolean, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Video(Base):
    """One uploaded lecture (video or audio). The admin tags the exam it belongs to
    (`exam_id` → drives the syllabus tree + knowledge set) plus topic/subtopic; content
    is organized by chapter → topic → subtopic within the exam, never by subject."""

    __tablename__ = "videos"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Which exam (and thus syllabus tree + knowledge set) this lecture maps to.
    exam_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("exams.id"), nullable=False)
    # Chapter is the PRIMARY retrieval dimension (CLAUDE.md §8): a lecture is uploaded under
    # one chapter, so Q&A knowledge retrieval filters Pinecone by it (like an MCQ document).
    chapter: Mapped[str | None] = mapped_column(String(500), nullable=True)
    topic: Mapped[str | None] = mapped_column(String(500), nullable=True)
    subtopic: Mapped[str | None] = mapped_column(String(500), nullable=True)
    custom_instruction: Mapped[str | None] = mapped_column(Text, nullable=True)

    file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"), nullable=True)
    audio_file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"), nullable=True)
    support_slides_file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"), nullable=True)

    # uploaded → extracting_audio → chunking_audio → transcribing → merging_transcript
    # → cleaning_transcript → generating_timeline → mapping_topics → generating_summary
    # → processing_slides → completed | failed
    processing_status: Mapped[str] = mapped_column(String(40), nullable=False, default="uploaded")
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_audio_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")  # draft | active | archived
    processing_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("processing_jobs.id", ondelete="SET NULL"), nullable=True)

    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class VideoAudioChunk(Base):
    """A time-bounded slice of the lecture audio (with overlap) sent to transcription.
    Global timestamp offsets are preserved so merged transcript times are absolute."""

    __tablename__ = "video_audio_chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    audio_file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")  # pending | transcribed | failed
    raw_transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_used: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class VideoTranscript(Base):
    __tablename__ = "video_transcripts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False)
    raw_merged_transcript: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cleaned_transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model_used_for_cleaning: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Merged transcription segments with absolute timestamps: [{start_seconds, end_seconds, text}]
    segments: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class VideoTimelineSegment(Base):
    """A meaningful teaching unit of the lecture. This is the primary retrieval index:
    its label/description drive the segment router; its summary + original_transcript
    are the primary answer grounding for Q&A."""

    __tablename__ = "video_timeline_segments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False)
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    label: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    original_transcript: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Chapter inherited from the parent video (the PRIMARY retrieval dimension);
    # topic/subtopic mapped from the live syllabus tree (validated; never invented).
    chapter: Mapped[str | None] = mapped_column(String(500), nullable=True)
    topic: Mapped[str | None] = mapped_column(String(500), nullable=True)
    subtopic_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # list of subtopic strings
    mapping_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class VideoSummary(Base):
    """The full lecture summary — passed into EVERY student Q&A request for global
    context (CLAUDE.md §13; this is a deliberate non-negotiable for answer quality)."""

    __tablename__ = "video_summaries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False)
    short_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    detailed_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    key_points: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    exam_focused_points: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    important_terms: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # {"mcqs": [...], "short": [...], "long": [...]}
    possible_questions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class VideoSupportSlide(Base):
    __tablename__ = "video_support_slides"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False)
    file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"), nullable=True)
    slide_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class VideoSlideLabel(Base):
    __tablename__ = "video_slide_labels"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False)
    slide_number: Mapped[int] = mapped_column(Integer, nullable=False)
    slide_id: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    related_timestamps: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # ["03:20-05:10", ...]
    topics: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class VideoChatSession(Base):
    __tablename__ = "video_chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False)
    student_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class VideoChatMessage(Base):
    __tablename__ = "video_chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_chat_sessions.id", ondelete="CASCADE"), nullable=False)
    video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False)
    student_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    language: Mapped[str | None] = mapped_column(String(30), nullable=True)
    selected_segment_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    detected_topic: Mapped[str | None] = mapped_column(String(500), nullable=True)
    detected_subtopic_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    sources_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    supporting_knowledge_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    follow_up_suggestions: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class VideoView(Base):
    __tablename__ = "video_views"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False)
    student_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    watch_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
