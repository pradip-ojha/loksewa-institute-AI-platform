import uuid
from datetime import datetime

from pydantic import BaseModel, model_validator


# ── Admin: list / detail ─────────────────────────────────────────────────────────

class VideoOut(BaseModel):
    id: uuid.UUID
    display_name: str
    content_usage_type: str
    topic: str | None = None
    subtopic: str | None = None
    processing_status: str
    status: str
    duration_seconds: int | None = None
    is_audio_only: bool = False
    has_slides: bool = False
    processing_job_id: uuid.UUID | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class VideoListOut(BaseModel):
    items: list[VideoOut]
    total: int
    page: int
    per_page: int


class TimelineSegmentOut(BaseModel):
    segment_id: str
    segment_index: int
    start_seconds: float
    end_seconds: float
    start_time: str
    end_time: str
    label: str
    description: str
    summary: str
    topic: str | None = None
    subtopic_ids: list[str] = []
    mapping_confidence: float | None = None


class SlideLabelOut(BaseModel):
    slide_number: int
    slide_id: str
    title: str
    related_timestamps: list[str] = []
    topics: list[str] = []
    summary: str


class SummaryOut(BaseModel):
    short_summary: str
    detailed_summary: str
    key_points: list[str] = []
    exam_focused_points: list[str] = []
    important_terms: list[str] = []
    possible_questions: dict = {}


class VideoDetailOut(VideoOut):
    custom_instruction: str | None = None
    media_url: str | None = None
    slides_url: str | None = None
    summary: SummaryOut | None = None
    timeline: list[TimelineSegmentOut] = []
    slides: list[SlideLabelOut] = []


# ── Student ──────────────────────────────────────────────────────────────────────

class StudentVideoListItem(BaseModel):
    id: uuid.UUID
    display_name: str
    topic: str | None = None
    subtopic: str | None = None
    duration_seconds: int | None = None
    is_audio_only: bool = False


class StudentPlayerData(BaseModel):
    id: uuid.UUID
    display_name: str
    media_url: str | None = None
    is_audio_only: bool = False
    duration_seconds: int | None = None
    summary: SummaryOut | None = None
    timeline: list[TimelineSegmentOut] = []
    slides: list[SlideLabelOut] = []


# ── Student: ask tutor ───────────────────────────────────────────────────────────

class AskRequest(BaseModel):
    question: str
    current_video_time: str | None = None   # "HH:MM:SS" or seconds-as-string
    chat_session_id: uuid.UUID | None = None


class AskSelectedSegment(BaseModel):
    segment_id: str
    label: str
    start_time: str
    end_time: str
    start_seconds: float


class SupportingKnowledge(BaseModel):
    chunk_id: str
    topic: str | None = None
    subtopic: str | None = None


class AskResponse(BaseModel):
    answer: str
    language: str
    chat_session_id: uuid.UUID
    selected_segments: list[AskSelectedSegment] = []
    detected_topic: str | None = None
    detected_subtopic_ids: list[str] = []
    supporting_knowledge_used: list[SupportingKnowledge] = []
    confidence: float = 0.0
    follow_up_suggestions: list[str] = []


class ChatMessageOut(BaseModel):
    id: uuid.UUID
    question: str
    answer: str
    language: str | None = None
    selected_segment_ids: list = []
    selected_segments: list[AskSelectedSegment] = []
    detected_topic: str | None = None
    confidence: float | None = None
    follow_up_suggestions: list = []
    created_at: datetime

    model_config = {"from_attributes": True}

    @model_validator(mode="before")
    @classmethod
    def _hydrate_segments(cls, m):
        """Pull the full clickable seek chips (label/start_time/start_seconds) out of
        the persisted sources_json so restored history turns stay seekable."""
        if isinstance(m, BaseModel) or isinstance(m, dict):
            return m
        sources = getattr(m, "sources_json", None) or {}
        return {
            "id": m.id,
            "question": m.question,
            "answer": m.answer,
            "language": m.language,
            "selected_segment_ids": m.selected_segment_ids or [],
            "selected_segments": sources.get("selected_segments", []),
            "detected_topic": m.detected_topic,
            "confidence": m.confidence,
            "follow_up_suggestions": m.follow_up_suggestions or [],
            "created_at": m.created_at,
        }
