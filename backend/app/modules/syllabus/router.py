import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_admin
from app.core.database import get_db
from app.core.exceptions import AppException
from app.modules.exams.service import get_exam_or_404
from app.modules.syllabus.models import SyllabusItem
from app.modules.syllabus.schemas import ChapterNode, SubtopicEntry, SyllabusTree, TopicNode
from app.modules.users.models import User

router = APIRouter(prefix="/admin/syllabus", tags=["syllabus"])


# ── helpers ───────────────────────────────────────────────────────────────────

async def _build_tree(db: AsyncSession, exam_id: uuid.UUID) -> SyllabusTree:
    result = await db.execute(
        select(SyllabusItem)
        .where(SyllabusItem.exam_id == exam_id, SyllabusItem.is_active == True)
        .order_by(SyllabusItem.sort_order)
    )
    items = result.scalars().all()

    # chapter → topic → [(id, subtopic)]
    tree: dict[str, dict[str, list[SubtopicEntry]]] = defaultdict(lambda: defaultdict(list))
    for item in items:
        if item.subtopic:
            tree[item.chapter][item.topic].append(SubtopicEntry(id=item.id, subtopic=item.subtopic))
        elif item.topic not in tree[item.chapter]:
            tree[item.chapter][item.topic] = []

    chapters = [
        ChapterNode(
            chapter=ch,
            topics=[TopicNode(topic=t, subtopics=subs) for t, subs in topics.items()],
        )
        for ch, topics in tree.items()
    ]
    return SyllabusTree(exam_id=exam_id, chapters=chapters)


# ── read ──────────────────────────────────────────────────────────────────────

@router.get("/exams/{exam_id}", response_model=SyllabusTree)
async def get_syllabus(exam_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    await get_exam_or_404(db, exam_id)
    return await _build_tree(db, exam_id)


# ── add item ──────────────────────────────────────────────────────────────────

class AddItemRequest(BaseModel):
    chapter: str
    topic: str
    subtopic: str | None = None


@router.post("/exams/{exam_id}/items", response_model=SyllabusTree, status_code=201)
async def add_item(
    exam_id: uuid.UUID,
    payload: AddItemRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    await get_exam_or_404(db, exam_id)
    result = await db.execute(
        select(SyllabusItem.sort_order)
        .where(SyllabusItem.exam_id == exam_id)
        .order_by(SyllabusItem.sort_order.desc())
        .limit(1)
    )
    max_sort = result.scalar_one_or_none() or 0

    db.add(SyllabusItem(
        id=uuid.uuid4(),
        exam_id=exam_id,
        chapter=payload.chapter.strip(),
        topic=payload.topic.strip(),
        subtopic=payload.subtopic.strip() if payload.subtopic else None,
        sort_order=max_sort + 1,
    ))
    await db.commit()
    return await _build_tree(db, exam_id)


# ── update single item ────────────────────────────────────────────────────────

class UpdateItemRequest(BaseModel):
    chapter: str | None = None
    topic: str | None = None
    subtopic: str | None = None


@router.put("/items/{item_id}", response_model=SyllabusTree)
async def update_item(
    item_id: uuid.UUID,
    payload: UpdateItemRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(SyllabusItem).where(SyllabusItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise AppException(404, "not_found", "Syllabus item not found.")
    if payload.chapter is not None:
        item.chapter = payload.chapter.strip()
    if payload.topic is not None:
        item.topic = payload.topic.strip()
    if payload.subtopic is not None:
        item.subtopic = payload.subtopic.strip() or None
    await db.commit()
    return await _build_tree(db, item.exam_id)


# ── delete single item ────────────────────────────────────────────────────────

@router.delete("/items/{item_id}", response_model=SyllabusTree)
async def delete_item(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(SyllabusItem).where(SyllabusItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise AppException(404, "not_found", "Syllabus item not found.")
    exam_id = item.exam_id
    await db.delete(item)
    await db.commit()
    return await _build_tree(db, exam_id)


# ── rename chapter (cascades to all rows in the exam) ─────────────────────────

class RenameChapterRequest(BaseModel):
    old_chapter: str
    new_chapter: str


@router.put("/exams/{exam_id}/chapter", response_model=SyllabusTree)
async def rename_chapter(
    exam_id: uuid.UUID,
    payload: RenameChapterRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(
        select(SyllabusItem)
        .where(SyllabusItem.exam_id == exam_id, SyllabusItem.chapter == payload.old_chapter)
    )
    items = result.scalars().all()
    if not items:
        raise AppException(404, "not_found", "Chapter not found.")
    for item in items:
        item.chapter = payload.new_chapter.strip()
    await db.commit()
    return await _build_tree(db, exam_id)


# ── delete chapter (cascades all topics/subtopics) ────────────────────────────

class ChapterRequest(BaseModel):
    chapter: str


@router.delete("/exams/{exam_id}/chapter", response_model=SyllabusTree)
async def delete_chapter(
    exam_id: uuid.UUID,
    payload: ChapterRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    await db.execute(
        delete(SyllabusItem).where(
            SyllabusItem.exam_id == exam_id,
            SyllabusItem.chapter == payload.chapter,
        )
    )
    await db.commit()
    return await _build_tree(db, exam_id)


# ── rename topic (within a chapter, cascades) ─────────────────────────────────

class RenameTopicRequest(BaseModel):
    chapter: str
    old_topic: str
    new_topic: str


@router.put("/exams/{exam_id}/topic", response_model=SyllabusTree)
async def rename_topic(
    exam_id: uuid.UUID,
    payload: RenameTopicRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(
        select(SyllabusItem).where(
            SyllabusItem.exam_id == exam_id,
            SyllabusItem.chapter == payload.chapter,
            SyllabusItem.topic == payload.old_topic,
        )
    )
    items = result.scalars().all()
    if not items:
        raise AppException(404, "not_found", "Topic not found.")
    for item in items:
        item.topic = payload.new_topic.strip()
    await db.commit()
    return await _build_tree(db, exam_id)


# ── delete topic (all subtopics under it) ─────────────────────────────────────

class TopicRequest(BaseModel):
    chapter: str
    topic: str


@router.delete("/exams/{exam_id}/topic", response_model=SyllabusTree)
async def delete_topic(
    exam_id: uuid.UUID,
    payload: TopicRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    await db.execute(
        delete(SyllabusItem).where(
            SyllabusItem.exam_id == exam_id,
            SyllabusItem.chapter == payload.chapter,
            SyllabusItem.topic == payload.topic,
        )
    )
    await db.commit()
    return await _build_tree(db, exam_id)
