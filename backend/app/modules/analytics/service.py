"""Admin analytics aggregation (read-only).

Three surfaces, all admin-scoped (every student's data, unlike the per-student
analytics in `mcq_tests.service.student_analytics`):
  • MCQ        — attempts, score distribution, topic/subtopic performance, weak spots
  • Subjective — submissions, marks distribution, per-test + per-question marks,
                 common mistakes, low-confidence count
  • Video      — views, questions, most-asked, unclear concepts, low-confidence answers

Subjective per-question stats are read out of the persisted reviewed `evaluation_data`
JSON (no separate per-question marks table exists), so aggregation happens in Python.
"""
import uuid
from collections import Counter, defaultdict

from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.models import User
from app.modules.mcq.models import MCQQuestion
from app.modules.mcq_tests.models import MCQAttempt, MCQAttemptAnswer
from app.modules.subjective.models import (
    SubjectiveTest, StudentAnswerSheet, AnswerEvaluation, PDFAnnotation,
)
from app.modules.video.models import Video, VideoView, VideoChatMessage

# Anything below this confidence is surfaced as "low confidence / unclear".
LOW_CONFIDENCE = 0.6
_MIN_SAMPLE = 2  # ignore tiny samples when flagging weak topics


def _pct(num: float, den: float) -> float:
    return round(num / den * 100.0, 1) if den else 0.0


# ── MCQ ───────────────────────────────────────────────────────────────────────

async def mcq_overview(db: AsyncSession) -> dict:
    att_r = await db.execute(
        select(MCQAttempt).where(MCQAttempt.status == "submitted")
    )
    attempts = att_r.scalars().all()

    percents = [_pct(a.correct_count, a.total_questions) for a in attempts if a.total_questions > 0]
    total_attempts = len(attempts)
    unique_students = len({a.student_id for a in attempts})

    summary = {
        "total_attempts": total_attempts,
        "total_students": unique_students,
        "average_score_percent": round(sum(percents) / len(percents), 1) if percents else 0.0,
        "highest_score_percent": round(max(percents), 1) if percents else 0.0,
        "lowest_score_percent": round(min(percents), 1) if percents else 0.0,
    }

    # Student-wise results.
    per_student: dict[uuid.UUID, list[float]] = defaultdict(list)
    for a in attempts:
        if a.total_questions > 0:
            per_student[a.student_id].append(_pct(a.correct_count, a.total_questions))
    student_results = []
    if per_student:
        users_r = await db.execute(select(User).where(User.id.in_(per_student.keys())))
        names = {u.id: u for u in users_r.scalars().all()}
        for sid, ps in per_student.items():
            u = names.get(sid)
            student_results.append({
                "student_id": str(sid),
                "student_name": u.full_name if u else "—",
                "student_email": u.email if u else "—",
                "attempts": len(ps),
                "average_percent": round(sum(ps) / len(ps), 1),
                "best_percent": round(max(ps), 1),
            })
        student_results.sort(key=lambda s: s["average_percent"], reverse=True)

    # Topic + subtopic correctness across every submitted answer.
    correct_expr = func.sum(case((MCQAttemptAnswer.is_correct.is_(True), 1), else_=0))
    grp_r = await db.execute(
        select(
            MCQQuestion.topic, MCQQuestion.subtopic,
            func.count().label("total"), correct_expr.label("correct"),
        )
        .join(MCQAttemptAnswer, MCQAttemptAnswer.question_id == MCQQuestion.id)
        .join(MCQAttempt, MCQAttempt.id == MCQAttemptAnswer.attempt_id)
        .where(MCQAttempt.status == "submitted")
        .group_by(MCQQuestion.topic, MCQQuestion.subtopic)
    )
    topic_agg: dict[str, dict] = defaultdict(lambda: {"total": 0, "correct": 0})
    subtopic_rows = []
    for topic, subtopic, total, correct in grp_r.all():
        total, correct = int(total or 0), int(correct or 0)
        t = topic or "Untagged"
        topic_agg[t]["total"] += total
        topic_agg[t]["correct"] += correct
        if subtopic:
            subtopic_rows.append({
                "topic": t, "subtopic": subtopic,
                "total": total, "correct": correct, "accuracy": _pct(correct, total),
            })

    topic_performance = [
        {"topic": t, "total": v["total"], "correct": v["correct"], "accuracy": _pct(v["correct"], v["total"])}
        for t, v in topic_agg.items()
    ]
    topic_performance.sort(key=lambda x: x["accuracy"])
    weak_subtopics = sorted(
        [s for s in subtopic_rows if s["accuracy"] < 60.0 and s["total"] >= _MIN_SAMPLE],
        key=lambda s: s["accuracy"],
    )

    # Per-question correct% — surface the hardest questions.
    q_r = await db.execute(
        select(
            MCQQuestion.id, MCQQuestion.question_text, MCQQuestion.topic, MCQQuestion.subtopic,
            func.count().label("total"), correct_expr.label("correct"),
        )
        .join(MCQAttemptAnswer, MCQAttemptAnswer.question_id == MCQQuestion.id)
        .join(MCQAttempt, MCQAttempt.id == MCQAttemptAnswer.attempt_id)
        .where(MCQAttempt.status == "submitted")
        .group_by(MCQQuestion.id, MCQQuestion.question_text, MCQQuestion.topic, MCQQuestion.subtopic)
    )
    question_performance = []
    for qid, text, topic, subtopic, total, correct in q_r.all():
        total, correct = int(total or 0), int(correct or 0)
        question_performance.append({
            "question_id": str(qid),
            "question_text": (text or "")[:160],
            "topic": topic or "Untagged",
            "subtopic": subtopic,
            "times_answered": total,
            "correct": correct,
            "accuracy": _pct(correct, total),
        })
    question_performance.sort(key=lambda q: q["accuracy"])
    hardest_questions = question_performance[:20]

    return {
        "summary": summary,
        "student_results": student_results,
        "topic_performance": topic_performance,
        "weak_subtopics": weak_subtopics,
        "hardest_questions": hardest_questions,
    }


# ── Subjective ────────────────────────────────────────────────────────────────

async def _latest_checked_evaluations(db: AsyncSession, test_id: uuid.UUID | None = None):
    """Latest reviewed evaluation per (checked) sheet, with the owning sheet + test.
    A student can re-upload, so we keep only the most recent sheet per student/test."""
    cond = [StudentAnswerSheet.current_status == "checked"]
    if test_id is not None:
        cond.append(StudentAnswerSheet.test_id == test_id)
    r = await db.execute(
        select(StudentAnswerSheet, AnswerEvaluation)
        .join(AnswerEvaluation, AnswerEvaluation.sheet_id == StudentAnswerSheet.id)
        .where(*cond)
        .order_by(StudentAnswerSheet.created_at.desc())
    )
    rows = r.all()
    # Dedup: keep most-recent sheet per (test, student); within a sheet keep newest eval.
    best: dict[tuple, tuple] = {}
    seen_sheets: set[uuid.UUID] = set()
    for sheet, ev in rows:
        if sheet.id in seen_sheets:
            continue
        seen_sheets.add(sheet.id)
        key = (sheet.test_id, sheet.student_id)
        if key not in best:  # rows are created_at desc → first seen is latest
            best[key] = (sheet, ev)
    return list(best.values())


async def subjective_overview(db: AsyncSession) -> dict:
    pairs = await _latest_checked_evaluations(db)

    tests_r = await db.execute(select(SubjectiveTest))
    tests = {t.id: t for t in tests_r.scalars().all()}

    per_test: dict[uuid.UUID, list] = defaultdict(list)
    students: set[uuid.UUID] = set()
    all_percents: list[float] = []
    low_conf = 0
    mistakes = Counter()

    for sheet, ev in pairs:
        per_test[sheet.test_id].append(ev)
        students.add(sheet.student_id)
        pct = _pct(ev.total_marks_awarded, ev.total_marks_possible)
        all_percents.append(pct)
        if ev.overall_confidence is not None and ev.overall_confidence < LOW_CONFIDENCE:
            low_conf += 1
        for qr in (ev.evaluation_data or {}).get("question_results", []):
            for mp in (qr.get("missing_points") or []):
                if isinstance(mp, str) and mp.strip():
                    mistakes[mp.strip()[:120]] += 1

    test_breakdown = []
    for tid, evs in per_test.items():
        t = tests.get(tid)
        pcts = [_pct(e.total_marks_awarded, e.total_marks_possible) for e in evs]
        awarded = [e.total_marks_awarded for e in evs]
        test_breakdown.append({
            "test_id": str(tid),
            "display_name": t.display_name if t else "—",
            "total_marks": t.total_marks if t else None,
            "submissions": len(evs),
            "average_percent": round(sum(pcts) / len(pcts), 1) if pcts else 0.0,
            "average_marks": round(sum(awarded) / len(awarded), 1) if awarded else 0.0,
            "highest_marks": round(max(awarded), 1) if awarded else 0.0,
            "lowest_marks": round(min(awarded), 1) if awarded else 0.0,
        })
    test_breakdown.sort(key=lambda x: x["display_name"])

    # Total submissions (all uploaded sheets, any status) + checked-PDF count.
    total_submissions = await db.execute(
        select(func.count()).select_from(StudentAnswerSheet)
    )
    checked_pdf_count = await db.execute(
        select(func.count()).select_from(PDFAnnotation).where(PDFAnnotation.checked_file_id.isnot(None))
    )

    return {
        "summary": {
            "total_submissions": int(total_submissions.scalar_one() or 0),
            "checked_submissions": len(pairs),
            "total_students": len(students),
            "average_percent": round(sum(all_percents) / len(all_percents), 1) if all_percents else 0.0,
            "highest_percent": round(max(all_percents), 1) if all_percents else 0.0,
            "lowest_percent": round(min(all_percents), 1) if all_percents else 0.0,
            "low_confidence_count": low_conf,
            "checked_pdf_count": int(checked_pdf_count.scalar_one() or 0),
        },
        "test_breakdown": test_breakdown,
        "common_mistakes": [{"text": m, "count": c} for m, c in mistakes.most_common(15)],
    }


async def subjective_test_detail(db: AsyncSession, test_id: uuid.UUID) -> dict:
    pairs = await _latest_checked_evaluations(db, test_id)

    student_ids = {sheet.student_id for sheet, _ in pairs}
    users = {}
    if student_ids:
        u_r = await db.execute(select(User).where(User.id.in_(student_ids)))
        users = {u.id: u for u in u_r.scalars().all()}

    student_results = []
    q_agg: dict[str, dict] = {}
    mistakes = Counter()
    for sheet, ev in pairs:
        u = users.get(sheet.student_id)
        student_results.append({
            "student_id": str(sheet.student_id),
            "student_name": u.full_name if u else "—",
            "student_email": u.email if u else "—",
            "marks_awarded": ev.total_marks_awarded,
            "marks_possible": ev.total_marks_possible,
            "percent": _pct(ev.total_marks_awarded, ev.total_marks_possible),
            "confidence": ev.overall_confidence,
        })
        for qr in (ev.evaluation_data or {}).get("question_results", []):
            qn = str(qr.get("question_number") or "?")
            slot = q_agg.setdefault(qn, {"awarded": 0.0, "max": 0.0, "n": 0})
            slot["awarded"] += float(qr.get("awarded_marks") or 0)
            slot["max"] += float(qr.get("max_marks") or 0)
            slot["n"] += 1
            for mp in (qr.get("missing_points") or []):
                if isinstance(mp, str) and mp.strip():
                    mistakes[mp.strip()[:120]] += 1

    student_results.sort(key=lambda s: s["percent"], reverse=True)
    question_performance = [
        {
            "question_number": qn,
            "submissions": v["n"],
            "average_awarded": round(v["awarded"] / v["n"], 2) if v["n"] else 0.0,
            "average_max": round(v["max"] / v["n"], 2) if v["n"] else 0.0,
            "accuracy": _pct(v["awarded"], v["max"]),
        }
        for qn, v in q_agg.items()
    ]
    question_performance.sort(key=lambda q: q["question_number"])

    t = await db.get(SubjectiveTest, test_id)
    return {
        "test_id": str(test_id),
        "display_name": t.display_name if t else "—",
        "submissions": len(pairs),
        "student_results": student_results,
        "question_performance": question_performance,
        "common_mistakes": [{"text": m, "count": c} for m, c in mistakes.most_common(15)],
    }


# ── Video ─────────────────────────────────────────────────────────────────────

async def video_overview(db: AsyncSession) -> list[dict]:
    vids_r = await db.execute(select(Video).order_by(Video.created_at.desc()))
    videos = vids_r.scalars().all()

    views_r = await db.execute(
        select(VideoView.video_id, func.count(), func.count(func.distinct(VideoView.student_id)))
        .group_by(VideoView.video_id)
    )
    views = {vid: (int(tot), int(uniq)) for vid, tot, uniq in views_r.all()}

    q_r = await db.execute(
        select(VideoChatMessage.video_id, func.count(),
               func.sum(case((VideoChatMessage.confidence < LOW_CONFIDENCE, 1), else_=0)))
        .group_by(VideoChatMessage.video_id)
    )
    questions = {vid: (int(tot or 0), int(low or 0)) for vid, tot, low in q_r.all()}

    out = []
    for v in videos:
        tot_views, uniq_viewers = views.get(v.id, (0, 0))
        tot_q, low_q = questions.get(v.id, (0, 0))
        out.append({
            "video_id": str(v.id),
            "display_name": v.display_name,
            "processing_status": v.processing_status,
            "status": v.status,
            "total_views": tot_views,
            "unique_viewers": uniq_viewers,
            "total_questions": tot_q,
            "low_confidence_answers": low_q,
        })
    return out


def _normalize_q(text: str) -> str:
    return " ".join((text or "").lower().split())


async def video_detail(db: AsyncSession, video_id: uuid.UUID) -> dict:
    v = await db.get(Video, video_id)

    views_r = await db.execute(
        select(func.count(), func.count(func.distinct(VideoView.student_id)))
        .where(VideoView.video_id == video_id)
    )
    tot_views, uniq_viewers = views_r.one()

    msgs_r = await db.execute(
        select(VideoChatMessage).where(VideoChatMessage.video_id == video_id)
        .order_by(VideoChatMessage.created_at.desc())
    )
    msgs = msgs_r.scalars().all()

    asked = Counter()
    canonical: dict[str, str] = {}
    per_student: dict[uuid.UUID, int] = defaultdict(int)
    unclear_topics = Counter()
    low_conf_answers = []
    for m in msgs:
        norm = _normalize_q(m.question)
        if norm:
            asked[norm] += 1
            canonical.setdefault(norm, m.question.strip())
        per_student[m.student_id] += 1
        if m.confidence is not None and m.confidence < LOW_CONFIDENCE:
            if m.detected_topic:
                unclear_topics[m.detected_topic] += 1
            if len(low_conf_answers) < 20:
                low_conf_answers.append({
                    "question": m.question[:200],
                    "confidence": m.confidence,
                    "detected_topic": m.detected_topic,
                    "created_at": m.created_at,
                })

    student_questions = []
    if per_student:
        u_r = await db.execute(select(User).where(User.id.in_(per_student.keys())))
        names = {u.id: u for u in u_r.scalars().all()}
        for sid, cnt in per_student.items():
            u = names.get(sid)
            student_questions.append({
                "student_id": str(sid),
                "student_name": u.full_name if u else "—",
                "student_email": u.email if u else "—",
                "questions": cnt,
            })
        student_questions.sort(key=lambda s: s["questions"], reverse=True)

    return {
        "video_id": str(video_id),
        "display_name": v.display_name if v else "—",
        "total_views": int(tot_views or 0),
        "unique_viewers": int(uniq_viewers or 0),
        "total_questions": len(msgs),
        "most_asked_questions": [
            {"question": canonical[n], "count": c} for n, c in asked.most_common(10)
        ],
        "unclear_concepts": [{"topic": t, "count": c} for t, c in unclear_topics.most_common(10)],
        "student_questions": student_questions,
        "low_confidence_answers": low_conf_answers,
    }
