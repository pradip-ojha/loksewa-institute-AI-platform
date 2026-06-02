import uuid
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class MCQOption(BaseModel):
    id: str
    label: str
    text: str


class MCQQuestionOut(BaseModel):
    id: uuid.UUID
    source_document_id: uuid.UUID | None
    review_batch_id: uuid.UUID | None
    origin_type: str
    question_text: str
    options: list[MCQOption]
    correct_option_ids: list[str]
    explanation: str | None
    chapter: str | None
    topic: str | None
    subtopic: str | None
    complexity: str
    status: str
    review_feedback: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MCQQuestionCreate(BaseModel):
    question_text: str
    options: list[MCQOption] = Field(min_length=2, max_length=4)
    correct_option_ids: list[str]
    explanation: str | None = None
    chapter: str | None = None
    topic: str | None = None
    subtopic: str | None = None
    complexity: Literal["easy", "medium", "hard"] = "medium"


class MCQQuestionUpdate(BaseModel):
    question_text: str | None = None
    options: list[MCQOption] | None = None
    correct_option_ids: list[str] | None = None
    explanation: str | None = None
    chapter: str | None = None
    topic: str | None = None
    subtopic: str | None = None
    complexity: Literal["easy", "medium", "hard"] | None = None


class MCQDocumentOut(BaseModel):
    id: uuid.UUID
    display_name: str
    origin_type: str
    file_id: uuid.UUID | None
    topic: str | None
    subtopic: str | None
    processing_status: str
    question_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class MCQReviewBatchOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID | None
    batch_type: str
    status: str
    total_questions: int
    accepted_count: int
    rejected_count: int
    rejection_feedback: str | None
    job_id: uuid.UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


class MCQBatchWithQuestions(MCQReviewBatchOut):
    questions: list[MCQQuestionOut]


class RejectQuestionRequest(BaseModel):
    feedback: str


class RejectAllRequest(BaseModel):
    feedback: str


class MCQQuestionListOut(BaseModel):
    items: list[MCQQuestionOut]
    total: int
    page: int
    per_page: int
