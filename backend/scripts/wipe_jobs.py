"""Wipe processing-job log rows.

Clears the `processing_jobs` history — the failed-job noise and any jobs stuck in
queued/processing/retrying. Tables that reference a job (subjective/video/mcq via
`*_job_id`) use ON DELETE SET NULL, so deleting job rows only nulls those pointers
and never removes real content.

Run from the backend/ directory:
    python -m scripts.wipe_jobs            # delete failed + stuck (everything NOT completed)
    python -m scripts.wipe_jobs --all      # delete ALL job rows (also completed history)
    python -m scripts.wipe_jobs --failed   # delete only failed jobs

NOTE: if a worker is genuinely mid-task, deleting its `processing` row loses that job's
tracking. Run this while things are idle (or right after stopping the worker).
"""
import argparse
import asyncio
import logging

from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.modules.jobs.models import JobStatus, ProcessingJob

logging.basicConfig(level=logging.INFO)

# "Everything not completed" = the failed logs + the stuck ones the admin sees as pending.
_NON_COMPLETED = [
    JobStatus.failed,
    JobStatus.queued,
    JobStatus.processing,
    JobStatus.retrying,
    JobStatus.cancelled,
]


async def main() -> None:
    parser = argparse.ArgumentParser(description="Wipe processing-job log rows.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="Delete ALL jobs, including completed history.")
    group.add_argument("--failed", action="store_true", help="Delete only failed jobs.")
    args = parser.parse_args()

    if args.all:
        statuses = list(JobStatus)
        label = "all"
    elif args.failed:
        statuses = [JobStatus.failed]
        label = "failed"
    else:
        statuses = _NON_COMPLETED
        label = "failed + stuck (non-completed)"

    async with AsyncSessionLocal() as db:
        # Per-status breakdown for a clear report of what is being removed.
        counts = {}
        for st in statuses:
            counts[st.value] = (await db.execute(
                select(func.count()).select_from(ProcessingJob).where(ProcessingJob.status == st)
            )).scalar_one()
        total = sum(counts.values())

        if total == 0:
            print(f"No {label} jobs to wipe.")
            return

        await db.execute(
            ProcessingJob.__table__.delete().where(ProcessingJob.status.in_(statuses))
        )
        await db.commit()

    print(f"Wiped {total} job(s) [{label}]:")
    for status, n in counts.items():
        if n:
            print(f"  {status:12} {n}")


if __name__ == "__main__":
    asyncio.run(main())
