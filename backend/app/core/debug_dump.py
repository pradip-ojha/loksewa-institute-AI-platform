"""Pipeline debug-dump utility.

When a pipeline's debug flag is enabled (MCQ_DEBUG_DUMP=1 or KNOWLEDGE_DEBUG_DUMP=1 in
.env), every call to dump_json / dump_bytes / dump_text writes a file into a per-job
subfolder under backend/debug_dumps/<pipeline>/. When disabled (the default, =0), every
call is a no-op with zero overhead.

Usage:
    from app.core.debug_dump import open_dump, dump_json, dump_bytes

    dump_dir = open_dump("mcq", str(job_id), "my_doc_name")   # None when disabled
    dump_json(dump_dir, "01_layout.json", layout_stats)
    dump_bytes(dump_dir, "02_page0.png", png_bytes)

Dump layout (example for MCQ):
    debug_dumps/
      mcq/
        20260717_143022_My_Doc_Name_a1b2c3d4/
          00_meta.json
          01_file_prep.json
          02_layout_decisions.json
          02_layout_stats.json
          03_regions/
            page0_region0.png
            page0_region1.png
            page1_region0.png
          04_vision/
            page0_region0.json
            page0_region1.json
          05_questions_flat.json
          06_chapter_groups.json
          07_final_questions.json
          08_output_reference.json
"""
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_DUMP_ROOT = Path(__file__).parent.parent.parent / "debug_dumps"


def _is_enabled(pipeline: str) -> bool:
    from app.core.config import settings
    if pipeline == "mcq":
        return getattr(settings, "MCQ_DEBUG_DUMP", 0) == 1
    if pipeline == "knowledge":
        return getattr(settings, "KNOWLEDGE_DEBUG_DUMP", 0) == 1
    return False


def open_dump(pipeline: str, job_id: str, label: str) -> Path | None:
    """Create and return the per-job dump directory, or None if dumping is disabled."""
    if not _is_enabled(pipeline):
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in label)[:40]
    d = _DUMP_ROOT / pipeline / f"{ts}_{safe}_{job_id[:8]}"
    try:
        d.mkdir(parents=True, exist_ok=True)
        logger.info("[debug_dump] %s pipeline dumping to: %s", pipeline, d)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[debug_dump] could not create dump dir %s: %s", d, exc)
        return None
    return d


def dump_json(dump_dir: Path | None, filename: str, data: object) -> None:
    if dump_dir is None:
        return
    target = dump_dir / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(target, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[debug_dump] failed to write %s: %s", target, exc)


def dump_bytes(dump_dir: Path | None, filename: str, data: bytes) -> None:
    if dump_dir is None:
        return
    target = dump_dir / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(target, "wb") as f:
            f.write(data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[debug_dump] failed to write %s: %s", target, exc)


def dump_text(dump_dir: Path | None, filename: str, text: str) -> None:
    if dump_dir is None:
        return
    target = dump_dir / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(target, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[debug_dump] failed to write %s: %s", target, exc)
