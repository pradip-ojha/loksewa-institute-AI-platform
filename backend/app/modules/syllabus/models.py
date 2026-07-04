import uuid

from sqlalchemy import String, Boolean, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SyllabusItem(Base):
    """One leaf of an exam's syllabus tree (chapter → topic → subtopic).

    Scoped by `exam_id` (the exam's `exam_type` supersedes the old `syllabus_type`).
    The tree is built per exam; there is no global objective/subjective split anymore.
    """

    __tablename__ = "syllabus_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    exam_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("exams.id", ondelete="CASCADE"), nullable=False)
    chapter: Mapped[str] = mapped_column(String(500), nullable=False)
    topic: Mapped[str] = mapped_column(String(500), nullable=False)
    subtopic: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
