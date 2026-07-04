import uuid
from datetime import datetime

from sqlalchemy import String, Float, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TutorChatSession(Base):
    """A standalone AI-tutor conversation for a student, scoped to ONE exam (ChatGPT-style
    sessions). The tutor answers from that exam's notes/book chunks plus the student's
    personalization data."""

    __tablename__ = "tutor_chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    exam_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("exams.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class TutorChatMessage(Base):
    __tablename__ = "tutor_chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tutor_chat_sessions.id", ondelete="CASCADE"), nullable=False)
    student_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    language: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # The topic-selector's routed demo mode: objective | subjective | shared.
    related_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    detected_topic: Mapped[str | None] = mapped_column(String(500), nullable=True)
    detected_subtopic_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    query_rewrite: Mapped[str | None] = mapped_column(Text, nullable=True)
    supporting_knowledge_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    follow_up_suggestions: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
