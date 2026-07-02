"""Reset exams to the real Banking 4th Level set.

Clears the old seeded exams + any exam-scoped content (mirrors the clean-slate wipe in
migration 015), then re-seeds the three real exams from app/seeds/*.json via
`seed_syllabus()`. Users / files / skills / jobs / ai_audit are intentionally preserved.

Run from the backend/ directory:
    python -m scripts.reset_exams
"""
import asyncio
import logging

from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.seeds.syllabus_seed import seed_syllabus

logging.basicConfig(level=logging.INFO)

# Exam-scoped content wiped so the old seeded exams leave nothing dangling. Same set as
# migration 015's clean-slate list, plus the exam + enrollment tables themselves.
# TRUNCATE ... CASCADE order-independent.
_WIPE_TABLES = (
    "syllabus_items",
    "knowledge_chunks", "knowledge_documents",
    "mcq_rejection_feedback", "mcq_questions", "mcq_review_batches", "mcq_documents",
    "mcq_attempt_answers", "mcq_attempts", "mcq_test_set_questions", "mcq_test_sets", "mcq_test_blueprints",
    "pdf_annotations", "answer_evaluations", "answer_extractions", "answer_quality_checks",
    "student_answer_sheets", "question_specific_checking_skills", "subjective_questions", "subjective_tests",
    "subjective_feedback_messages", "subjective_feedback_chats",
    "video_chat_messages", "video_chat_sessions", "video_views", "video_slide_labels",
    "video_support_slides", "video_summaries", "video_timeline_segments", "video_transcripts",
    "video_audio_chunks", "videos",
    "tutor_chat_messages", "tutor_chat_sessions",
    "student_exam_enrollments", "exams",
)


async def main() -> None:
    async with AsyncSessionLocal() as db:
        before = (await db.execute(text("SELECT count(*) FROM exams"))).scalar()
        await db.execute(text("TRUNCATE TABLE " + ", ".join(_WIPE_TABLES) + " CASCADE"))
        await db.commit()
        print(f"Wiped {before} old exam(s) + all exam-scoped content.")

    # seed_syllabus() is guarded to no-op when exams already exist; the wipe above cleared
    # them, so this now creates the three real exams + their full syllabus trees.
    await seed_syllabus()

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text(
            "SELECT e.exam_type, e.name, count(s.id) AS items "
            "FROM exams e LEFT JOIN syllabus_items s ON s.exam_id = e.id "
            "GROUP BY e.id, e.exam_type, e.name ORDER BY e.created_at"
        ))).all()
        print("\n=== Seeded exams ===")
        for r in rows:
            print(f"  {r.exam_type:11} | {r.name:35} | {r.items} syllabus items")


if __name__ == "__main__":
    asyncio.run(main())
