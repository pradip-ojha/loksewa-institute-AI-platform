import json
import logging
import uuid
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.modules.exams.models import Exam
from app.modules.syllabus.models import SyllabusItem
from app.modules.users.models import User, UserRole

logger = logging.getLogger("neurafix")

SEEDS_DIR = Path(__file__).parent

# Default exams created on first startup so the platform boots with a usable baseline.
# These are the real Banking 4th Level (unified examination system) exams — one objective
# pre-test and the two subjective papers, each strictly ONE type. Admins add more from the UI.
_DEFAULT_EXAMS = [
    ("subjective", "Banking 4th Level First Paper", "banking_first_paper_subjective.json"),
    ("objective", "Banking 4th Level Pretest", "banking_pretest_objective.json"),
    ("subjective", "Banking 4th Level Second Paper", "banking_second_paper_subjective.json"),
]


async def seed_syllabus() -> None:
    async with AsyncSessionLocal() as db:
        if (await db.execute(select(Exam).limit(1))).scalar_one_or_none():
            return  # exams (and their syllabus) already seeded

        admin = (await db.execute(
            select(User).where(User.role == UserRole.institute_admin).order_by(User.created_at).limit(1)
        )).scalar_one_or_none()
        if admin is None:
            # Fall back to the configured default admin email if the role lookup misses.
            admin = (await db.execute(
                select(User).where(User.email == settings.DEFAULT_ADMIN_EMAIL).limit(1)
            )).scalar_one_or_none()
        if admin is None:
            logger.warning("No admin user found; skipping exam/syllabus seed.")
            return

        for exam_type, name, filename in _DEFAULT_EXAMS:
            exam = Exam(id=uuid.uuid4(), exam_type=exam_type, name=name, created_by=admin.id)
            db.add(exam)
            await db.flush()  # assign exam.id for the syllabus rows

            data = json.loads((SEEDS_DIR / filename).read_text(encoding="utf-8"))
            sort = 0
            for chapter_block in data:
                chapter = chapter_block["chapter"]
                for topic_block in chapter_block["topics"]:
                    topic = topic_block["topic"]
                    subtopics = topic_block.get("subtopics", [])
                    if subtopics:
                        for subtopic in subtopics:
                            db.add(SyllabusItem(
                                id=uuid.uuid4(), exam_id=exam.id, chapter=chapter,
                                topic=topic, subtopic=subtopic, sort_order=sort,
                            ))
                            sort += 1
                    else:
                        db.add(SyllabusItem(
                            id=uuid.uuid4(), exam_id=exam.id, chapter=chapter,
                            topic=topic, subtopic=None, sort_order=sort,
                        ))
                        sort += 1

        await db.commit()
        logger.info("Seeded %d default exams + their syllabus.", len(_DEFAULT_EXAMS))
