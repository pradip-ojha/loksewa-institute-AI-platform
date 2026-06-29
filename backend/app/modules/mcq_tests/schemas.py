import uuid
from datetime import datetime
from pydantic import BaseModel, Field, field_validator

from app.modules.mcq.schemas import MCQOption, normalize_options


# ── Blueprint ─────────────────────────────────────────────────────────────────

class TopicDistEntry(BaseModel):
    # Chapter is the PRIMARY distribution dimension (CLAUDE.md §10): admins pick how many
    # questions come from each chapter, optionally narrowed by a topic/subtopic within it.
    chapter: str = Field(min_length=1)
    topic: str | None = None
    subtopic: str | None = None
    count: int = Field(ge=1, le=200)


class DifficultyDist(BaseModel):
    easy: int = Field(default=0, ge=0)
    medium: int = Field(default=0, ge=0)
    hard: int = Field(default=0, ge=0)

    def total(self) -> int:
        return self.easy + self.medium + self.hard


class BlueprintCreate(BaseModel):
    exam_id: uuid.UUID
    test_name: str = Field(min_length=1, max_length=255)
    total_time_minutes: int = Field(ge=1, le=600)
    num_sets: int = Field(ge=1, le=50)
    topic_distribution: list[TopicDistEntry] = Field(min_length=1)
    difficulty_distribution: DifficultyDist | None = None
    custom_instruction: str | None = None


class BlueprintOut(BaseModel):
    id: uuid.UUID
    test_name: str
    total_time_minutes: int
    num_sets: int
    topic_distribution: list
    difficulty_distribution: dict | None
    custom_instruction: str | None
    status: str
    generation_result: dict | None
    job_id: uuid.UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


class BlueprintListOut(BaseModel):
    items: list[BlueprintOut]
    total: int
    page: int
    per_page: int


# ── Test sets ─────────────────────────────────────────────────────────────────

class TestSetOut(BaseModel):
    id: uuid.UUID
    blueprint_id: uuid.UUID
    set_name: str
    num_questions: int
    difficulty_mix: dict | None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TestSetListOut(BaseModel):
    items: list[TestSetOut]
    total: int
    page: int
    per_page: int


# A question as the admin previews it — full detail incl. correct answers.
class PreviewQuestion(BaseModel):
    id: uuid.UUID
    question_text: str
    options: list[MCQOption]
    correct_option_ids: list[str]
    explanation: str | None
    topic: str | None
    subtopic: str | None
    complexity: str
    question_order: int

    @field_validator("options", mode="before")
    @classmethod
    def coerce_options(cls, v):
        return normalize_options(v)


class TestSetPreview(TestSetOut):
    test_name: str
    total_time_minutes: int
    questions: list[PreviewQuestion]


# ── Student-facing (no answers leaked) ────────────────────────────────────────

class StudentTestSetOut(BaseModel):
    """A test as a student sees it in the list — no questions yet. Carries this
    student's own attempt state so the UI shows Start / Continue / View Result."""
    set_id: uuid.UUID
    test_name: str
    set_name: str
    total_time_minutes: int
    num_questions: int
    attempt_status: str  # "none" | "in_progress" | "submitted"
    attempt_id: uuid.UUID | None = None
    score: int | None = None
    correct_count: int | None = None


class StudentQuestion(BaseModel):
    id: uuid.UUID
    question_text: str
    options: list[MCQOption]
    topic: str | None
    complexity: str
    question_order: int

    @field_validator("options", mode="before")
    @classmethod
    def coerce_options(cls, v):
        return normalize_options(v)


class AttemptStartOut(BaseModel):
    attempt_id: uuid.UUID
    set_id: uuid.UUID
    test_name: str
    total_time_minutes: int
    started_at: datetime
    questions: list[StudentQuestion]


class SubmittedAnswer(BaseModel):
    question_id: uuid.UUID
    selected_option_id: str | None = None


class AttemptSubmit(BaseModel):
    answers: list[SubmittedAnswer] = Field(default_factory=list)
    time_taken_seconds: int | None = None


class ResultQuestion(BaseModel):
    id: uuid.UUID
    question_text: str
    options: list[MCQOption]
    correct_option_ids: list[str]
    selected_option_id: str | None
    is_correct: bool
    explanation: str | None
    topic: str | None
    complexity: str
    question_order: int

    @field_validator("options", mode="before")
    @classmethod
    def coerce_options(cls, v):
        return normalize_options(v)


class AttemptResultOut(BaseModel):
    attempt_id: uuid.UUID
    set_id: uuid.UUID
    test_name: str
    status: str
    score: int
    total_questions: int
    correct_count: int
    time_taken_seconds: int | None
    submitted_at: datetime | None
    questions: list[ResultQuestion]


# ── Student history + analytics ───────────────────────────────────────────────

class AttemptHistoryItem(BaseModel):
    attempt_id: uuid.UUID
    set_id: uuid.UUID
    test_name: str
    set_name: str
    score: int
    total_questions: int
    correct_count: int
    time_taken_seconds: int | None
    submitted_at: datetime | None


class TopicPerformance(BaseModel):
    topic: str
    total: int
    correct: int
    accuracy: float


class StudentAnalyticsOut(BaseModel):
    total_attempts: int
    total_questions_answered: int
    total_correct: int
    overall_accuracy: float
    average_score_percent: float
    best_score_percent: float | None
    topic_performance: list[TopicPerformance]
    weak_topics: list[TopicPerformance]
