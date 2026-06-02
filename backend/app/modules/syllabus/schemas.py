import uuid
from pydantic import BaseModel
from app.modules.syllabus.models import SyllabusType


class SyllabusItemOut(BaseModel):
    id: uuid.UUID
    syllabus_type: SyllabusType
    chapter: str
    topic: str
    subtopic: str | None
    sort_order: int

    model_config = {"from_attributes": True}


class SubtopicEntry(BaseModel):
    id: uuid.UUID
    subtopic: str


class TopicNode(BaseModel):
    topic: str
    subtopics: list[SubtopicEntry]  # includes id so frontend can delete/edit individual items


class ChapterNode(BaseModel):
    chapter: str
    topics: list[TopicNode]


class SyllabusTree(BaseModel):
    syllabus_type: SyllabusType
    chapters: list[ChapterNode]
