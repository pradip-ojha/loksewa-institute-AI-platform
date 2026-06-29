import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ExamCreate(BaseModel):
    exam_type: str = Field(..., description="objective | subjective")
    name: str
    description: str | None = None


class ExamUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None  # active | archived


class ExamOut(BaseModel):
    id: uuid.UUID
    exam_type: str
    name: str
    description: str | None
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class EnrollmentOut(BaseModel):
    exam_id: uuid.UUID
    exam_type: str
    name: str
    enrolled_at: datetime
