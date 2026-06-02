import enum
import uuid

from sqlalchemy import String, Boolean, Integer, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SyllabusType(str, enum.Enum):
    objective = "objective"
    subjective = "subjective"


class SyllabusItem(Base):
    __tablename__ = "syllabus_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    syllabus_type: Mapped[SyllabusType] = mapped_column(SAEnum(SyllabusType, name="syllabus_type"), nullable=False)
    chapter: Mapped[str] = mapped_column(String(500), nullable=False)
    topic: Mapped[str] = mapped_column(String(500), nullable=False)
    subtopic: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
