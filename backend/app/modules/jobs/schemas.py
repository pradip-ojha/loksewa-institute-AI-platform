import uuid
from datetime import datetime
from pydantic import BaseModel
from app.modules.jobs.models import JobStatus


class JobOut(BaseModel):
    id: uuid.UUID
    job_type: str
    status: JobStatus
    progress_percent: int
    current_step: str | None
    error_message: str | None
    output_reference: dict | None = None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    model_config = {"from_attributes": True}
