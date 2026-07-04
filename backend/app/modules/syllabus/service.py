"""Syllabus service helpers.

Most syllabus logic lives inline in `router.py` (single-leaf CRUD + cascade
rename/delete). This module holds the one operation that is shared beyond the
router — bulk-replacing an exam's whole syllabus tree — used by the
PDF-import background job (`SyllabusExtractionAgent`) to populate a freshly
created exam without the admin entering every leaf by hand.
"""
import uuid

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.syllabus.models import SyllabusItem


async def replace_syllabus_tree(
    db: AsyncSession,
    exam_id: uuid.UUID,
    tree: list[dict],
) -> dict:
    """Replace ALL syllabus items for `exam_id` with the given chapter/topic/subtopic tree.

    `tree` is `[{chapter, topics: [{topic, subtopics: [str, ...]}]}]` — the same shape
    as the JSON seeds. One `SyllabusItem` row is written per subtopic; a topic with no
    subtopics becomes a single row with `subtopic=None` (same denormalized leaf model the
    seed loop uses in `app/seeds/syllabus_seed.py`).

    The existing rows are deleted first, so the whole operation is idempotent — a Celery
    retry that re-enters this phase replaces rather than duplicates. It runs on a freshly
    created (or explicitly re-imported) exam, so replacing is the intended behaviour.

    Returns `{chapters, topics, subtopics}` insert counts for the job's output_reference.
    """
    await db.execute(delete(SyllabusItem).where(SyllabusItem.exam_id == exam_id))

    chapters = 0
    topics = 0
    subtopics = 0
    sort = 0
    for chapter_block in tree:
        chapter = (chapter_block.get("chapter") or "").strip()
        if not chapter:
            continue
        chapter_topics = chapter_block.get("topics") or []
        if not chapter_topics:
            continue
        chapters += 1
        for topic_block in chapter_topics:
            topic = (topic_block.get("topic") or "").strip()
            if not topic:
                continue
            topics += 1
            subs = [
                s.strip()
                for s in (topic_block.get("subtopics") or [])
                if isinstance(s, str) and s.strip()
            ]
            if subs:
                for subtopic in subs:
                    db.add(SyllabusItem(
                        id=uuid.uuid4(), exam_id=exam_id, chapter=chapter,
                        topic=topic, subtopic=subtopic, sort_order=sort,
                    ))
                    subtopics += 1
                    sort += 1
            else:
                db.add(SyllabusItem(
                    id=uuid.uuid4(), exam_id=exam_id, chapter=chapter,
                    topic=topic, subtopic=None, sort_order=sort,
                ))
                sort += 1

    await db.commit()
    return {"chapters": chapters, "topics": topics, "subtopics": subtopics}
