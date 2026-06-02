import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_admin
from app.core.database import get_db
from app.core.exceptions import AppException
from app.modules.syllabus.models import SyllabusItem, SyllabusType
from app.modules.syllabus.schemas import ChapterNode, SubtopicEntry, SyllabusTree, TopicNode
from app.modules.users.models import User

router = APIRouter(prefix="/admin/syllabus", tags=["syllabus"])


# ── helpers ───────────────────────────────────────────────────────────────────

async def _build_tree(db: AsyncSession, syllabus_type: SyllabusType) -> SyllabusTree:
    result = await db.execute(
        select(SyllabusItem)
        .where(SyllabusItem.syllabus_type == syllabus_type, SyllabusItem.is_active == True)
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
    return SyllabusTree(syllabus_type=syllabus_type, chapters=chapters)


def _type(raw: str) -> SyllabusType:
    try:
        return SyllabusType(raw)
    except ValueError:
        raise AppException(404, "invalid_type", "syllabus_type must be 'objective' or 'subjective'")


# ── read ──────────────────────────────────────────────────────────────────────

@router.get("/objective", response_model=SyllabusTree)
async def get_objective(db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    return await _build_tree(db, SyllabusType.objective)


@router.get("/subjective", response_model=SyllabusTree)
async def get_subjective(db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    return await _build_tree(db, SyllabusType.subjective)


# ── add item ──────────────────────────────────────────────────────────────────

class AddItemRequest(BaseModel):
    chapter: str
    topic: str
    subtopic: str | None = None


@router.post("/{syllabus_type}/items", response_model=SyllabusTree, status_code=201)
async def add_item(
    syllabus_type: str,
    payload: AddItemRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    stype = _type(syllabus_type)
    # get max sort_order
    result = await db.execute(
        select(SyllabusItem.sort_order)
        .where(SyllabusItem.syllabus_type == stype)
        .order_by(SyllabusItem.sort_order.desc())
        .limit(1)
    )
    max_sort = result.scalar_one_or_none() or 0

    db.add(SyllabusItem(
        id=uuid.uuid4(),
        syllabus_type=stype,
        chapter=payload.chapter.strip(),
        topic=payload.topic.strip(),
        subtopic=payload.subtopic.strip() if payload.subtopic else None,
        sort_order=max_sort + 1,
    ))
    await db.commit()
    return await _build_tree(db, stype)


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
    return await _build_tree(db, item.syllabus_type)


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
    stype = item.syllabus_type
    await db.delete(item)
    await db.commit()
    return await _build_tree(db, stype)


# ── rename chapter (cascades to all rows) ─────────────────────────────────────

class RenameChapterRequest(BaseModel):
    old_chapter: str
    new_chapter: str


@router.put("/{syllabus_type}/chapter", response_model=SyllabusTree)
async def rename_chapter(
    syllabus_type: str,
    payload: RenameChapterRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    stype = _type(syllabus_type)
    result = await db.execute(
        select(SyllabusItem)
        .where(SyllabusItem.syllabus_type == stype, SyllabusItem.chapter == payload.old_chapter)
    )
    items = result.scalars().all()
    if not items:
        raise AppException(404, "not_found", "Chapter not found.")
    for item in items:
        item.chapter = payload.new_chapter.strip()
    await db.commit()
    return await _build_tree(db, stype)


# ── delete chapter (cascades all topics/subtopics) ────────────────────────────

class ChapterRequest(BaseModel):
    chapter: str


@router.delete("/{syllabus_type}/chapter", response_model=SyllabusTree)
async def delete_chapter(
    syllabus_type: str,
    payload: ChapterRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    stype = _type(syllabus_type)
    await db.execute(
        delete(SyllabusItem).where(
            SyllabusItem.syllabus_type == stype,
            SyllabusItem.chapter == payload.chapter,
        )
    )
    await db.commit()
    return await _build_tree(db, stype)


# ── rename topic (within a chapter, cascades) ─────────────────────────────────

class RenameTopicRequest(BaseModel):
    chapter: str
    old_topic: str
    new_topic: str


@router.put("/{syllabus_type}/topic", response_model=SyllabusTree)
async def rename_topic(
    syllabus_type: str,
    payload: RenameTopicRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    stype = _type(syllabus_type)
    result = await db.execute(
        select(SyllabusItem).where(
            SyllabusItem.syllabus_type == stype,
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
    return await _build_tree(db, stype)


# ── delete topic (all subtopics under it) ─────────────────────────────────────

class TopicRequest(BaseModel):
    chapter: str
    topic: str


@router.delete("/{syllabus_type}/topic", response_model=SyllabusTree)
async def delete_topic(
    syllabus_type: str,
    payload: TopicRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    stype = _type(syllabus_type)
    await db.execute(
        delete(SyllabusItem).where(
            SyllabusItem.syllabus_type == stype,
            SyllabusItem.chapter == payload.chapter,
            SyllabusItem.topic == payload.topic,
        )
    )
    await db.commit()
    return await _build_tree(db, stype)
