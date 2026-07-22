import uuid
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, field_validator


def normalize_options(options) -> list[dict]:
    """
    Normalize MCQ options to the canonical list format regardless of what the AI returned.
    Handles:
      - Correct list:  [{"id": "A", "label": "A", "text": "..."}, ...]
      - Simple dict:   {"A": "text", "B": "text", ...}
      - Nested dict:   {"A": {"text": "...", ...}, ...}
    """
    if isinstance(options, list):
        normalized = []
        for item in options:
            if isinstance(item, dict) and "id" in item and "text" in item:
                normalized.append({
                    "id": item["id"],
                    "label": item.get("label", item["id"]),
                    "text": str(item["text"]),
                })
        return normalized

    if isinstance(options, dict):
        result = []
        for key in ("A", "B", "C", "D"):
            val = options.get(key)
            if val is None:
                continue
            if isinstance(val, str):
                text = val
            elif isinstance(val, dict):
                text = val.get("text", str(val))
            else:
                text = str(val)
            result.append({"id": key, "label": key, "text": text})
        return result

    return []


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

    @field_validator("options", mode="before")
    @classmethod
    def coerce_options(cls, v):
        return normalize_options(v)

    model_config = {"from_attributes": True}


class MCQQuestionCreate(BaseModel):
    exam_id: uuid.UUID
    question_text: str
    options: list[MCQOption] = Field(min_length=2, max_length=4)
    correct_option_ids: list[str]
    explanation: str | None = None
    # Chapter is the PRIMARY syllabus dimension and is required so every question can be
    # placed in a chapter-scoped test set (CLAUDE.md §9, §10).
    chapter: str = Field(min_length=1)
    topic: str | None = None
    subtopic: str | None = None
    complexity: Literal["easy", "medium", "hard"] = "medium"

    @field_validator("options", mode="before")
    @classmethod
    def coerce_options(cls, v):
        return normalize_options(v)


class MCQQuestionUpdate(BaseModel):
    question_text: str | None = None
    options: list[MCQOption] | None = None
    correct_option_ids: list[str] | None = None
    explanation: str | None = None
    chapter: str | None = None
    topic: str | None = None
    subtopic: str | None = None
    complexity: Literal["easy", "medium", "hard"] | None = None

    @field_validator("options", mode="before")
    @classmethod
    def coerce_options(cls, v):
        if v is None:
            return v
        return normalize_options(v)


class MCQDocumentOut(BaseModel):
    id: uuid.UUID
    display_name: str
    origin_type: str
    exam_id: uuid.UUID
    file_id: uuid.UUID | None
    chapter: str | None
    topic: str | None
    subtopic: str | None
    answer_format: str
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
