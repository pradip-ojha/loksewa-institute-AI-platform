import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SkillListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    agent_type: str
    active_version_number: int | None = None
    instruction_text: str = ""
    activated_at: datetime | None = None


class SkillVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    version_number: int
    instruction_text: str
    status: str
    change_summary: str | None = None
    created_at: datetime
    activated_at: datetime | None = None


class SkillDetailOut(BaseModel):
    agent_type: str
    active: SkillVersionOut | None = None
    history: list[SkillVersionOut] = []


class ChatMessageOut(BaseModel):
    role: str
    content: str


class ChatStartIn(BaseModel):
    agent_type: str = Field(..., min_length=1)


class ChatStartOut(BaseModel):
    chat_id: uuid.UUID
    agent_type: str
    messages: list[ChatMessageOut] = []


class DraftOut(BaseModel):
    version_id: uuid.UUID
    version_number: int
    instruction_text: str
    change_summary: str = ""


class ChatMessageIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


class ChatReplyOut(BaseModel):
    chat_id: uuid.UUID
    reply: str
    draft: DraftOut | None = None


class ApproveOut(BaseModel):
    agent_type: str
    active: SkillVersionOut | None = None
