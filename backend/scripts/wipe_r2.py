"""Wipe every object from the R2 bucket + clear the now-orphaned `files` table.

After the exam/architecture reset, all content tables (knowledge/mcq/subjective/video)
are empty, so every `files` row and every R2 object is a leftover from prior testing.
This deletes all bucket objects and truncates `files` so storage and DB return to a clean,
consistent empty state. Users / skills / jobs / ai_audit are untouched.

Run from the backend/ directory:
    python -m scripts.wipe_r2
"""
import asyncio

from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.integrations.r2_client import get_r2


async def main() -> None:
    # Safety check: refuse to clear `files` if any content row still references a file,
    # so this can only ever run as a true clean-slate.
    async with AsyncSessionLocal() as db:
        refs = 0
        for tbl, col in [
            ("knowledge_documents", "file_id"), ("mcq_documents", "file_id"),
            ("subjective_tests", "question_paper_file_id"), ("videos", "file_id"),
        ]:
            refs += (await db.execute(text(f"SELECT count(*) FROM {tbl} WHERE {col} IS NOT NULL"))).scalar() or 0
        if refs:
            raise SystemExit(f"Aborting: {refs} content row(s) still reference files — not a clean slate.")

    r2 = get_r2()
    keys = r2.list_all_keys()
    print(f"Found {len(keys)} object(s) in bucket '{r2.bucket}'.")
    if keys:
        deleted = r2.delete_objects(keys)
        print(f"Deleted {deleted} object(s) from R2.")

    async with AsyncSessionLocal() as db:
        n = (await db.execute(text("SELECT count(*) FROM files"))).scalar()
        await db.execute(text("TRUNCATE TABLE files CASCADE"))
        await db.commit()
        print(f"Cleared {n} orphaned row(s) from the files table.")

    remaining = len(get_r2().list_all_keys())
    print(f"Bucket now holds {remaining} object(s).")


if __name__ == "__main__":
    asyncio.run(main())
