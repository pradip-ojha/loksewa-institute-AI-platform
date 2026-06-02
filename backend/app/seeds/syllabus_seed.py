import json
import logging
import uuid
from pathlib import Path

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.modules.syllabus.models import SyllabusItem, SyllabusType

logger = logging.getLogger("neurafix")

SEEDS_DIR = Path(__file__).parent


async def seed_syllabus() -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(SyllabusItem).limit(1))
        if result.scalar_one_or_none():
            return  # already seeded

        for syllabus_type, filename in [
            (SyllabusType.objective, "objective_syllabus.json"),
            (SyllabusType.subjective, "subjective_syllabus.json"),
        ]:
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
                                id=uuid.uuid4(),
                                syllabus_type=syllabus_type,
                                chapter=chapter,
                                topic=topic,
                                subtopic=subtopic,
                                sort_order=sort,
                            ))
                            sort += 1
                    else:
                        db.add(SyllabusItem(
                            id=uuid.uuid4(),
                            syllabus_type=syllabus_type,
                            chapter=chapter,
                            topic=topic,
                            subtopic=None,
                            sort_order=sort,
                        ))
                        sort += 1

        await db.commit()
        logger.info("Syllabus seeded: objective + subjective.")
