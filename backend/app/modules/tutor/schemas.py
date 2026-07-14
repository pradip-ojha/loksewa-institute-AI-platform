import uuid
from datetime import datetime

from pydantic import BaseModel


class SupportingKnowledge(BaseModel):
    chunk_id: str
    topic: str | None = None
    subtopic: str | None = None


class TutorAskRequest(BaseModel):
    question: str
    exam_id: uuid.UUID | None = None      # required to START a new chat; omitted when resuming
    chat_session_id: uuid.UUID | None = None


class TutorAskResponse(BaseModel):
    answer: str
    language: str
    chat_session_id: uuid.UUID
    related_mode: str | None = None
    detected_topic: str | None = None
    detected_subtopic_ids: list[str] = []
    supporting_knowledge_used: list[SupportingKnowledge] = []
    confidence: float = 0.0
    selection_confidence: float = 0.0
    follow_up_suggestions: list[str] = []


class TutorChatMessageOut(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    question: str
    answer: str
    language: str | None = None
    related_mode: str | None = None
    detected_topic: str | None = None
    follow_up_suggestions: list[str] = []
    created_at: datetime

    model_config = {"from_attributes": True}
