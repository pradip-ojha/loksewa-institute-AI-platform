"""Personalization update tasks (CLAUDE.md §Personalization, spec §4.2).

These run the AI roll-ups OFF the request path: FastAPI handlers and the subjective
checking task enqueue them (fire-and-forget) so a student's submit/chat stays fast. They
are NOT tracked jobs (no `processing_jobs` row) — like the reaper, they run their work
directly on the worker's persistent loop and are entirely best-effort.

Triggers:
  • pers_update_daily        — after each activity (MCQ submit / subjective check)
  • pers_update_subjective   — immediately after each subjective test (extended summary)
  • pers_update_chat         — after a chatbot turn (session summary + every-5-Q-A daily roll-up)
  • pers_nightly_compress    — beat: expire prior-day raw activity detail
  • pers_weekly              — beat: weekly summary + intro refresh for active students
"""
import asyncio
import logging
import uuid

from workers.celery_app import celery_app
from workers.runtime import get_loop, run_async

logger = logging.getLogger(__name__)

# Bound on waiting for a roll-up submitted to an already-running loop. Generous —
# the weekly refresh makes one AI call per student. On timeout only the WAIT is
# abandoned; the coroutine itself keeps running on the loop.
_PERS_WAIT_SECONDS = 600


def _run(coro_factory) -> None:
    """Run a best-effort personalization coroutine on the persistent loop.

    If the loop is already running (another thread is driving it), submit the
    coroutine thread-safely and wait bounded instead of dropping the tick — the old
    unconditional skip silently lost per-turn/per-activity roll-ups under load, so
    summaries drifted stale until the nightly beat."""
    try:
        loop = get_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(coro_factory(), loop).result(timeout=_PERS_WAIT_SECONDS)
        else:
            run_async(coro_factory())
    except Exception as exc:  # never let personalization crash a worker
        logger.warning("personalization task failed: %s", exc)


@celery_app.task(name="workers.tasks.personalization_tasks.pers_update_daily", queue="kvi_ai_default", ignore_result=True)
def pers_update_daily(student_id: str) -> None:
    async def work():
        from app.core.database import AsyncSessionLocal
        from app.modules.personalization import service as svc
        async with AsyncSessionLocal() as db:
            await svc.recompute_daily_summary(db, uuid.UUID(student_id))
    _run(work)


@celery_app.task(name="workers.tasks.personalization_tasks.pers_update_subjective", queue="kvi_ai_default", ignore_result=True)
def pers_update_subjective(student_id: str, test_text: str) -> None:
    async def work():
        from app.core.database import AsyncSessionLocal
        from app.modules.personalization import service as svc
        async with AsyncSessionLocal() as db:
            await svc.recompute_extended_subjective(db, uuid.UUID(student_id), test_text)
            await svc.recompute_daily_summary(db, uuid.UUID(student_id))
    _run(work)


@celery_app.task(name="workers.tasks.personalization_tasks.pers_update_chat", queue="kvi_ai_default", ignore_result=True)
def pers_update_chat(student_id: str, session_kind: str, session_id: str, turns: str) -> None:
    async def work():
        from app.core.database import AsyncSessionLocal
        from app.modules.personalization import service as svc
        async with AsyncSessionLocal() as db:
            await svc.summarize_chat_session(
                db, student_id=uuid.UUID(student_id), session_kind=session_kind,
                session_id=uuid.UUID(session_id), turns=turns,
            )
    _run(work)


@celery_app.task(name="workers.tasks.personalization_tasks.pers_nightly_compress", queue="kvi_ai_default", ignore_result=True)
def pers_nightly_compress() -> None:
    async def work():
        from app.core.database import AsyncSessionLocal
        from app.modules.personalization import service as svc
        async with AsyncSessionLocal() as db:
            n = await svc.nightly_compress(db)
            if n:
                logger.info("personalization nightly compress: distilled %d activity log(s)", n)
    _run(work)


@celery_app.task(name="workers.tasks.personalization_tasks.pers_weekly", queue="kvi_ai_default", ignore_result=True)
def pers_weekly() -> None:
    async def work():
        from app.core.database import AsyncSessionLocal
        from app.modules.personalization import service as svc
        async with AsyncSessionLocal() as db:
            n = await svc.generate_weekly_all(db)
            if n:
                logger.info("personalization weekly: refreshed %d student(s)", n)
    _run(work)
