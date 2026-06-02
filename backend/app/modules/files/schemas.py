import uuid
from datetime import datetime
from pydantic import BaseModel


class FileOut(BaseModel):
    id: uuid.UUID
    original_filename: str
    display_name: str
    mime_type: str
    file_size: int
    r2_key: str
    uploaded_by: uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}


class FileWithUrl(FileOut):
    signed_url: str
