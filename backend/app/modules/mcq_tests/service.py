"""MCQ test-set generation + student attempt logic.

Set generation (run inside a Celery job) draws questions from the *approved*
question pool to satisfy a blueprint's topic/subtopic + difficulty distribution.
Two hard rules from CLAUDE.md §10:
  • All sets in one batch share NO questions (cross-set uniqueness).
  • If the pool can't satisfy the distribution, report a shortage breakdown and
    create nothing — never auto-generate or borrow from nearby topics.
"""
import random
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, func, case
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.mcq.models import MCQQuestion
from app.modules.mcq_tests.models import (
    MCQTestBlueprint, MCQTestSet, MCQTestSetQuestion, MCQAttempt, MCQAttemptAnswer,
)


# ── Leaf-bucket planning ──────────────────────────────────────────────────────

def _split_by_difficulty(count: int, diff: dict | None) -> list[tuple[str | None, int]]:
    """Split a topic bucket's per-set count across difficulties.

    `topic_distribution` is authoritative for how many questions each
    topic/subtopic contributes; the difficulty distribution is applied as a
    proportional preference *within* that count so the two views stay
    consistent. Returns [(complexity|None, count), ...] summing to `count`.
    """
    if not diff:
        return [(None, count)]
    total = (diff.get("easy", 0) or 0) + (diff.get("medium", 0) or 0) + (diff.get("hard", 0) or 0)
    if total <= 0:
        return [(None, count)]

    order = ["easy", "medium", "hard"]
    raw = {d: count * (diff.get(d, 0) or 0) / total for d in order}
    alloc = {d: int(raw[d]) for d in order}
    # Distribute the rounding remainder to the largest fractional parts so the
    # per-bucket total is exactly preserved.
    remaining = count - sum(alloc.values())
    for d in sorted(order, key=lambda d: raw[d] - int(raw[d]), reverse=True):
        if remaining <= 0:
            break
        alloc[d] += 1
        remaining -= 1
    return [(d, n) for d, n in alloc.items() if n > 0]


def _build_buckets(blueprint: MCQTestBlueprint) -> list[dict]:
    """Flatten the blueprint into leaf buckets: one (chapter, topic, subtopic, complexity)
    requirement with a per-set count. Chapter is the primary dimension."""
    buckets: list[dict] = []
    diff = blueprint.difficulty_distribution
    for entry in blueprint.topic_distribution or []:
        chapter = entry.get("chapter") or None
        topic = entry.get("topic") or None
        subtopic = entry.get("subtopic") or None
        count = int(entry.get("count") or 0)
        if count <= 0:
            continue
        for complexity, n in _split_by_difficulty(count, diff):
            buckets.append({
                "chapter": chapter, "topic": topic, "subtopic": subtopic,
                "complexity": complexity, "per_set": n,
            })
    return buckets


async def _approved_ids(db: AsyncSession, bucket: dict, exclude: set[uuid.UUID], exam_id) -> list[uuid.UUID]:
    q = select(MCQQuestion.id).where(
        MCQQuestion.status == "approved",
        MCQQuestion.exam_id == exam_id,
    )
    if bucket.get("chapter"):
        q = q.where(MCQQuestion.chapter == bucket["chapter"])
    if bucket["topic"]:
        q = q.where(MCQQuestion.topic == bucket["topic"])
    if bucket["subtopic"]:
        q = q.where(MCQQuestion.subtopic == bucket["subtopic"])
    if bucket["complexity"]:
        q = q.where(MCQQuestion.complexity == bucket["complexity"])
    rows = await db.execute(q)
    ids = [r for r in rows.scalars().all() if r not in exclude]
    random.shuffle(ids)
    return ids


async def generate_sets(db: AsyncSession, blueprint: MCQTestBlueprint) -> dict:
    """Plan + persist test sets for a blueprint. Returns a result dict that is
    stored on the blueprint and the job's output_reference.

    On shortage: persists nothing, sets blueprint.status='shortage'.
    On success: creates `num_sets` sets (status 'draft') with unique questions,
    sets blueprint.status='generated'.
    """
    num_sets = blueprint.num_sets
    buckets = _build_buckets(blueprint)
    if not buckets:
        result = {"generated": False, "reason": "empty_distribution", "shortages": []}
        blueprint.status = "shortage"
        blueprint.generation_result = result
        await db.commit()
        return result

    used: set[uuid.UUID] = set()
    # plan[set_index] = list of (question_id, complexity)
    plan: dict[int, list[tuple[uuid.UUID, str | None]]] = {i: [] for i in range(num_sets)}
    shortages: list[dict] = []

    for bucket in buckets:
        per_set = bucket["per_set"]
        required_total = per_set * num_sets
        ids = await _approved_ids(db, bucket, used, blueprint.exam_id)
        take = ids[:required_total]
        # Reserve whatever is available so later buckets don't double-count it,
        # keeping the shortage report internally consistent.
        used.update(take)
        if len(ids) < required_total:
            shortages.append({
                "chapter": bucket.get("chapter"),
                "topic": bucket["topic"],
                "subtopic": bucket["subtopic"],
                "complexity": bucket["complexity"],
                "required": required_total,
                "available": len(ids),
                "shortage": required_total - len(ids),
            })
            continue
        for i, qid in enumerate(take):
            set_index = i // per_set
            plan[set_index].append((qid, bucket["complexity"]))

    if shortages:
        result = {"generated": False, "reason": "insufficient_questions", "shortages": shortages}
        blueprint.status = "shortage"
        blueprint.generation_result = result
        await db.commit()
        return result

    # Persist all sets + their questions in a single commit so a partial set
    # batch can never land. Remove any sets from a previous run of this
    # blueprint first (re-generation / retry).
    old = await db.execute(select(MCQTestSet).where(MCQTestSet.blueprint_id == blueprint.id))
    for s in old.scalars().all():
        await db.delete(s)

    created = 0
    for set_index in range(num_sets):
        entries = plan[set_index]
        random.shuffle(entries)
        mix: dict[str, int] = {}
        test_set = MCQTestSet(
            blueprint_id=blueprint.id,
            exam_id=blueprint.exam_id,
            set_name=f"{blueprint.test_name} — Set {set_index + 1}",
            num_questions=len(entries),
            status="draft",
        )
        db.add(test_set)
        await db.flush()
        for order, (qid, complexity) in enumerate(entries):
            db.add(MCQTestSetQuestion(set_id=test_set.id, question_id=qid, question_order=order))
            if complexity:
                mix[complexity] = mix.get(complexity, 0) + 1
        test_set.difficulty_mix = mix or None
        created += 1

    blueprint.status = "generated"
    blueprint.generation_result = {"generated": True, "sets_created": created, "shortages": []}
    await db.commit()

    return {"generated": True, "sets_created": num_sets, "shortages": []}


# ── Admin queries ─────────────────────────────────────────────────────────────

async def get_blueprint(db: AsyncSession, blueprint_id: uuid.UUID) -> MCQTestBlueprint | None:
    r = await db.execute(select(MCQTestBlueprint).where(MCQTestBlueprint.id == blueprint_id))
    return r.scalar_one_or_none()


async def list_blueprints(db: AsyncSession, page: int = 1, per_page: int = 20):
    total = (await db.execute(select(func.count()).select_from(MCQTestBlueprint))).scalar_one()
    r = await db.execute(
        select(MCQTestBlueprint).order_by(MCQTestBlueprint.created_at.desc())
        .offset((page - 1) * per_page).limit(per_page)
    )
    return r.scalars().all(), total


async def list_sets(db: AsyncSession, *, blueprint_id: uuid.UUID | None, status: str | None,
                    page: int = 1, per_page: int = 20):
    q = select(MCQTestSet)
    if blueprint_id:
        q = q.where(MCQTestSet.blueprint_id == blueprint_id)
    if status:
        q = q.where(MCQTestSet.status == status)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    r = await db.execute(q.order_by(MCQTestSet.created_at.desc()).offset((page - 1) * per_page).limit(per_page))
    return r.scalars().all(), total


async def get_set(db: AsyncSession, set_id: uuid.UUID) -> MCQTestSet | None:
    r = await db.execute(select(MCQTestSet).where(MCQTestSet.id == set_id))
    return r.scalar_one_or_none()


async def get_set_questions(db: AsyncSession, set_id: uuid.UUID) -> list[tuple[MCQQuestion, int]]:
    """Return (question, order) pairs for a set, ordered."""
    r = await db.execute(
        select(MCQQuestion, MCQTestSetQuestion.question_order)
        .join(MCQTestSetQuestion, MCQTestSetQuestion.question_id == MCQQuestion.id)
        .where(MCQTestSetQuestion.set_id == set_id)
        .order_by(MCQTestSetQuestion.question_order)
    )
    return [(row[0], row[1]) for row in r.all()]


async def set_status(db: AsyncSession, set_id: uuid.UUID, status: str) -> MCQTestSet | None:
    s = await get_set(db, set_id)
    if not s:
        return None
    s.status = status
    await db.commit()
    await db.refresh(s)
    return s


async def delete_set(db: AsyncSession, set_id: uuid.UUID) -> bool:
    s = await get_set(db, set_id)
    if not s:
        return False
    await db.delete(s)
    await db.commit()
    return True


# ── Student attempt flow ──────────────────────────────────────────────────────

async def list_student_tests(db: AsyncSession, student_id: uuid.UUID, exam_id: uuid.UUID | None = None):
    """Tests a student can see: every active set, PLUS any set this student
    already has an attempt on (so an in-progress attempt stays visible even if
    the admin deactivated the set mid-attempt). Each item carries the student's
    own attempt state so the UI can show Start / Continue / View Result.
    ``exam_id`` narrows to the student's currently-selected exam (shared exam
    picker across MCQ/Video/Subjective/AI Tutor)."""
    # This student's attempts, keyed by set.
    att_r = await db.execute(
        select(MCQAttempt).where(MCQAttempt.student_id == student_id)
    )
    attempts = {a.set_id: a for a in att_r.scalars().all()}

    # Active sets within the student's ENROLLED exams, PLUS the sets this student
    # already has an attempt on (so an in-progress attempt stays visible).
    from app.modules.exams.service import get_enrolled_exam_ids
    enrolled = await get_enrolled_exam_ids(db, student_id)
    set_ids = set(attempts.keys())
    cond = (MCQTestSet.status == "active") & MCQTestSet.exam_id.in_(enrolled or [uuid.uuid4()])
    if set_ids:
        cond = cond | MCQTestSet.id.in_(set_ids)
    if exam_id is not None:
        cond = cond & (MCQTestSet.exam_id == exam_id)
    r = await db.execute(
        select(MCQTestSet, MCQTestBlueprint)
        .join(MCQTestBlueprint, MCQTestBlueprint.id == MCQTestSet.blueprint_id)
        .where(cond)
        .order_by(MCQTestSet.created_at.desc())
    )
    out = []
    for s, bp in r.all():
        a = attempts.get(s.id)
        status = a.status if a else "none"  # "none" | "in_progress" | "submitted"
        out.append({
            "set_id": s.id,
            "test_name": bp.test_name,
            "set_name": s.set_name,
            "total_time_minutes": bp.total_time_minutes,
            "num_questions": s.num_questions,
            "attempt_status": status,
            "attempt_id": a.id if a else None,
            "score": a.score if (a and a.status == "submitted") else None,
            "correct_count": a.correct_count if (a and a.status == "submitted") else None,
        })
    return out


async def _get_blueprint_for_set(db: AsyncSession, set_id: uuid.UUID) -> MCQTestBlueprint | None:
    r = await db.execute(
        select(MCQTestBlueprint)
        .join(MCQTestSet, MCQTestSet.blueprint_id == MCQTestBlueprint.id)
        .where(MCQTestSet.id == set_id)
    )
    return r.scalar_one_or_none()


async def get_student_attempt(db: AsyncSession, set_id: uuid.UUID, student_id: uuid.UUID) -> MCQAttempt | None:
    r = await db.execute(
        select(MCQAttempt).where(MCQAttempt.set_id == set_id, MCQAttempt.student_id == student_id)
    )
    return r.scalar_one_or_none()


async def get_or_create_attempt(db: AsyncSession, test_set: MCQTestSet, student_id: uuid.UUID) -> MCQAttempt:
    """Return this student's attempt for the set, creating one if none exists.

    Race-safe: two near-simultaneous 'start' requests both see no attempt and
    both try to INSERT; the DB unique constraint (uq_attempt_student_set) rejects
    the second, which we catch and turn into a fetch of the row the winner
    created — so a duplicate start resumes the same attempt instead of 500ing.
    """
    existing = await get_student_attempt(db, test_set.id, student_id)
    if existing:
        return existing

    attempt = MCQAttempt(
        set_id=test_set.id,
        student_id=student_id,
        total_questions=test_set.num_questions,
        status="in_progress",
    )
    db.add(attempt)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await get_student_attempt(db, test_set.id, student_id)
        if existing:
            return existing
        raise
    await db.refresh(attempt)
    return attempt


def _is_correct(selected: str | None, correct_ids: list[str]) -> bool:
    if selected is None:
        return False
    return {selected} == set(correct_ids or [])


async def submit_attempt(
    db: AsyncSession, attempt: MCQAttempt, answers: list, time_taken_seconds: int | None,
) -> None:
    """Grade + persist an in-progress attempt. No negative marking: score equals
    the number of correct answers."""
    pairs = await get_set_questions(db, attempt.set_id)
    selected_map = {str(a.question_id): a.selected_option_id for a in answers}

    # Grade + persist answers + finalize the attempt in a single commit (atomic).
    correct_count = 0
    for question, _order in pairs:
        sel = selected_map.get(str(question.id))
        ok = _is_correct(sel, question.correct_option_ids)
        if ok:
            correct_count += 1
        db.add(MCQAttemptAnswer(
            attempt_id=attempt.id,
            question_id=question.id,
            selected_option_id=sel,
            is_correct=ok,
        ))
    attempt.correct_count = correct_count
    attempt.score = correct_count
    attempt.total_questions = len(pairs)
    attempt.time_taken_seconds = time_taken_seconds
    attempt.submitted_at = datetime.now(timezone.utc)
    attempt.status = "submitted"
    await db.commit()

    # ── Personalization (best-effort; never block/break a submit) ────────────────
    try:
        await _log_mcq_activity(db, attempt, pairs, selected_map, correct_count)
    except Exception:  # noqa: BLE001
        pass


async def _log_mcq_activity(db, attempt, pairs, selected_map, correct_count) -> None:
    """Record the MCQ attempt as a personalization activity + enqueue the daily roll-up."""
    from app.modules.personalization import service as pers
    # Per-topic correctness → highlights weak topics for the tutor.
    topics: dict[str, list[int]] = {}
    for question, _order in pairs:
        t = question.topic or "general"
        ok = _is_correct(selected_map.get(str(question.id)), question.correct_option_ids)
        bucket = topics.setdefault(t, [0, 0])
        bucket[0] += 1 if ok else 0
        bucket[1] += 1
    weak = [t for t, (c, n) in topics.items() if n and c / n < 0.6]
    total = len(pairs)
    test_set = (await db.execute(select(MCQTestSet).where(MCQTestSet.id == attempt.set_id))).scalar_one_or_none()
    raw = {
        "score": correct_count, "total": total,
        "per_topic": {t: {"correct": c, "total": n} for t, (c, n) in topics.items()},
        "weak_topics": weak,
    }
    summary = (f"Objective test '{test_set.set_name if test_set else 'MCQ'}': {correct_count}/{total} correct"
               + (f"; weak: {', '.join(weak)}" if weak else ""))
    await pers.log_activity(
        db, student_id=attempt.student_id, activity_type="mcq_test", entity_id=attempt.id,
        exam_id=(test_set.exam_id if test_set else None), raw_context=raw, summary_line=summary,
    )
    try:
        from app.core.celery_client import get_celery
        get_celery().send_task(
            "workers.tasks.personalization_tasks.pers_update_daily",
            args=[str(attempt.student_id)], queue="kvi_ai_default",
        )
    except Exception:  # noqa: BLE001
        pass


async def get_attempt(db: AsyncSession, attempt_id: uuid.UUID) -> MCQAttempt | None:
    r = await db.execute(select(MCQAttempt).where(MCQAttempt.id == attempt_id))
    return r.scalar_one_or_none()


async def build_result(db: AsyncSession, attempt: MCQAttempt) -> dict:
    """Assemble the full result payload (questions + correct answers + the
    student's selections + explanations) for a submitted attempt."""
    bp = await _get_blueprint_for_set(db, attempt.set_id)
    pairs = await get_set_questions(db, attempt.set_id)
    ans_r = await db.execute(
        select(MCQAttemptAnswer).where(MCQAttemptAnswer.attempt_id == attempt.id)
    )
    ans_map = {str(a.question_id): a for a in ans_r.scalars().all()}

    questions = []
    for question, order in pairs:
        a = ans_map.get(str(question.id))
        questions.append({
            "id": question.id,
            "question_text": question.question_text,
            "options": question.options,
            "correct_option_ids": question.correct_option_ids,
            "selected_option_id": a.selected_option_id if a else None,
            "is_correct": a.is_correct if a else False,
            "explanation": question.explanation,
            "topic": question.topic,
            "complexity": question.complexity,
            "question_order": order,
        })
    return {
        "attempt_id": attempt.id,
        "set_id": attempt.set_id,
        "test_name": bp.test_name if bp else "",
        "status": attempt.status,
        "score": attempt.score,
        "total_questions": attempt.total_questions,
        "correct_count": attempt.correct_count,
        "time_taken_seconds": attempt.time_taken_seconds,
        "submitted_at": attempt.submitted_at,
        "questions": questions,
    }


async def attempt_history(db: AsyncSession, student_id: uuid.UUID) -> list[dict]:
    """A student's submitted attempts, newest first — for the Results/History tab."""
    r = await db.execute(
        select(MCQAttempt, MCQTestBlueprint.test_name, MCQTestSet.set_name)
        .join(MCQTestSet, MCQTestSet.id == MCQAttempt.set_id)
        .join(MCQTestBlueprint, MCQTestBlueprint.id == MCQTestSet.blueprint_id)
        .where(MCQAttempt.student_id == student_id, MCQAttempt.status == "submitted")
        .order_by(MCQAttempt.submitted_at.desc())
    )
    out = []
    for attempt, test_name, set_name in r.all():
        out.append({
            "attempt_id": attempt.id,
            "set_id": attempt.set_id,
            "test_name": test_name,
            "set_name": set_name,
            "score": attempt.score,
            "total_questions": attempt.total_questions,
            "correct_count": attempt.correct_count,
            "time_taken_seconds": attempt.time_taken_seconds,
            "submitted_at": attempt.submitted_at,
        })
    return out


async def student_analytics(db: AsyncSession, student_id: uuid.UUID) -> dict:
    """Personal MCQ analytics for a single student (own data only): overall
    accuracy, average/best score, and per-topic performance to surface weak
    topics. Computed only over submitted attempts."""
    # Submitted attempts for averages / bests.
    att_r = await db.execute(
        select(MCQAttempt).where(
            MCQAttempt.student_id == student_id, MCQAttempt.status == "submitted"
        )
    )
    attempts = att_r.scalars().all()

    percents = [
        (a.correct_count / a.total_questions * 100.0)
        for a in attempts if a.total_questions > 0
    ]
    total_attempts = len(attempts)
    average_score_percent = round(sum(percents) / len(percents), 1) if percents else 0.0
    best_score_percent = round(max(percents), 1) if percents else None

    # Per-topic correctness across all this student's submitted answers.
    correct_expr = func.sum(case((MCQAttemptAnswer.is_correct.is_(True), 1), else_=0))
    topic_r = await db.execute(
        select(
            MCQQuestion.topic,
            func.count().label("total"),
            correct_expr.label("correct"),
        )
        .join(MCQAttemptAnswer, MCQAttemptAnswer.question_id == MCQQuestion.id)
        .join(MCQAttempt, MCQAttempt.id == MCQAttemptAnswer.attempt_id)
        .where(MCQAttempt.student_id == student_id, MCQAttempt.status == "submitted")
        .group_by(MCQQuestion.topic)
    )
    topic_performance = []
    total_answered = 0
    total_correct = 0
    for topic, total, correct in topic_r.all():
        total = int(total or 0)
        correct = int(correct or 0)
        total_answered += total
        total_correct += correct
        topic_performance.append({
            "topic": topic or "Untagged",
            "total": total,
            "correct": correct,
            "accuracy": round(correct / total * 100.0, 1) if total else 0.0,
        })
    topic_performance.sort(key=lambda t: t["accuracy"])

    # Weak topics: below 60% accuracy with at least a little data, weakest first.
    weak_topics = [t for t in topic_performance if t["accuracy"] < 60.0 and t["total"] >= 2]

    overall_accuracy = round(total_correct / total_answered * 100.0, 1) if total_answered else 0.0

    return {
        "total_attempts": total_attempts,
        "total_questions_answered": total_answered,
        "total_correct": total_correct,
        "overall_accuracy": overall_accuracy,
        "average_score_percent": average_score_percent,
        "best_score_percent": best_score_percent,
        "topic_performance": topic_performance,
        "weak_topics": weak_topics,
    }
