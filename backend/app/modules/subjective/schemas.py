import uuid
from datetime import datetime

from pydantic import BaseModel


# ── Admin: tests ───────────────────────────────────────────────────────────────

class SubjectiveQuestionOut(BaseModel):
    id: uuid.UUID
    question_number: str
    question_text: str
    marks: int
    question_order: int
    topic: str | None = None
    subtopic: str | None = None

    model_config = {"from_attributes": True}


class SubjectiveTestOut(BaseModel):
    id: uuid.UUID
    display_name: str
    total_time_minutes: int
    num_questions: int
    total_marks: int
    status: str
    skill_generation_status: str
    skill_generation_job_id: uuid.UUID | None
    has_rubric: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class SubjectiveTestDetailOut(SubjectiveTestOut):
    custom_instruction: str | None = None
    question_paper_url: str | None = None
    model_answer_url: str | None = None
    rubric_url: str | None = None
    questions: list[SubjectiveQuestionOut] = []


class TestListOut(BaseModel):
    items: list[SubjectiveTestOut]
    total: int
    page: int
    per_page: int


# ── Admin: submissions ─────────────────────────────────────────────────────────

class SubmissionOut(BaseModel):
    sheet_id: uuid.UUID
    student_id: uuid.UUID
    student_name: str
    student_email: str
    upload_attempt_number: int
    current_status: str
    total_marks_awarded: float | None = None
    total_marks_possible: float | None = None
    checked_pdf_url: str | None = None
    created_at: datetime


# ── Student ────────────────────────────────────────────────────────────────────

class StudentTestListItem(BaseModel):
    test_id: uuid.UUID
    display_name: str
    total_time_minutes: int
    num_questions: int
    total_marks: int
    # none | uploaded | processing | needs_reupload | checked | failed
    submission_status: str
    sheet_id: uuid.UUID | None = None
    upload_attempt_number: int = 0
    total_marks_awarded: float | None = None


class StudentTestDetailOut(BaseModel):
    test_id: uuid.UUID
    display_name: str
    total_time_minutes: int
    num_questions: int
    total_marks: int
    question_paper_url: str | None = None
    submission_status: str
    sheet_id: uuid.UUID | None = None
    upload_attempt_number: int = 0


class QualityResultOut(BaseModel):
    overall_status: str
    readability_score: float | None = None
    quality_notes: str | None = None


class ResultQuestionOut(BaseModel):
    question_number: str
    question_text: str
    marks_awarded: float
    marks_possible: float
    feedback: str | None = None
    mistakes: list[str] = []


class AnswerResultOut(BaseModel):
    sheet_id: uuid.UUID
    test_id: uuid.UUID
    display_name: str
    status: str  # processing | needs_reupload | checked | failed
    upload_attempt_number: int
    can_reupload: bool
    total_marks_awarded: float | None = None
    total_marks_possible: float | None = None
    quality: QualityResultOut | None = None
    questions: list[ResultQuestionOut] = []
    checked_pdf_url: str | None = None
