"""AI page-layout classifier for AMBIGUOUS typed pages (hybrid column detection).

The deterministic detector (app/processing/page_layout.py) resolves most pages by itself
(split / whole). Only pages it flags "ambiguous" are sent here: a cheap gpt-5-mini
(`get_provider("routing")`) vision call decides single- vs two-column. If the mini
deployment cannot accept images (404) the call falls back to gpt-5
(`get_provider("vision_typed")`, the deployment that already does all typed OCR) — the 404
is memoized per job so it is hit at most once. If AI is unavailable entirely, the page is
rendered WHOLE: a false split scrambles reading order and poisons chunks, a missed split
only lowers OCR fidelity.

Plain functions, deliberately NOT skill-tunable (structural extraction, like
KnowledgeProcessingAgent / SyllabusExtractionAgent). Must not import the knowledge agent
(the knowledge agent imports THIS module).
"""
from __future__ import annotations

import asyncio
import logging

from app.ai.model_router import get_provider
from app.processing.page_layout import (
    PageLayout,
    analyze_pdf_layouts,
)

logger = logging.getLogger(__name__)

CLASSIFY_CONCURRENCY = 6   # platform-wide AI fan-out convention

LAYOUT_CLASSIFY_PROMPT = """You are a page-layout classifier. Look at this page image and decide whether the BODY text is laid out in ONE column or TWO side-by-side columns.
- "two_column" means two parallel vertical blocks of running text: the left column is read top-to-bottom first, then the right column.
- A table, a centered heading, a title/cover page, a figure, a form, or an indented list is NOT two columns.
- If two_column, give the x-position of the vertical whitespace gutter between the columns, normalized 0-1000 (left page edge = 0, right page edge = 1000).
Return ONLY valid JSON: {"layout": "single_column" | "two_column", "gutter_x": <integer 0-1000 or null>}"""

# AI-provided gutters outside this range are more plausibly a wide indent/margin than a
# real column boundary — never split on them (matches WIDE_BAND in page_layout.py).
_AI_GUTTER_RANGE = (0.30, 0.70)


def _parse_classification(result: dict) -> dict | None:
    """Validate the model payload. Returns {"layout": ..., "gutter_x": int|None} or None."""
    layout = result.get("layout")
    if layout not in ("single_column", "two_column"):
        return None
    gutter_x = result.get("gutter_x")
    if gutter_x is not None:
        if not isinstance(gutter_x, (int, float)) or not (0 <= gutter_x <= 1000):
            return None
        gutter_x = int(gutter_x)
    return {"layout": layout, "gutter_x": gutter_x}


async def classify_page_layout(
    png: bytes, *, state: dict, audit_ctx: dict | None = None
) -> dict | None:
    """Classify one ambiguous page image. `state` is the per-job memo
    ({"mini_supports_images": bool}). Returns the validated payload, or None when every
    provider failed (caller renders whole — the safe fallback)."""
    if state.get("mini_supports_images", True):
        try:
            result = await get_provider("routing").generate_with_image(
                LAYOUT_CLASSIFY_PROMPT, png, schema={}, audit_ctx=audit_ctx
            )
            parsed = _parse_classification(result)
            if parsed is not None:
                return parsed
            logger.warning("gpt-5-mini layout classification returned an invalid payload — trying gpt-5")
        except Exception as exc:
            if "404" in str(exc):
                state["mini_supports_images"] = False
                logger.warning(
                    "gpt-5-mini deployment rejected image input (404) — "
                    "falling back to gpt-5 for the rest of this job"
                )
            else:
                logger.warning("gpt-5-mini layout classification failed: %s — trying gpt-5", exc)

    try:
        result = await get_provider("vision_typed").generate_with_image(
            LAYOUT_CLASSIFY_PROMPT, png, schema={}, audit_ctx=audit_ctx
        )
        parsed = _parse_classification(result)
        if parsed is not None:
            return parsed
        logger.warning("gpt-5 layout classification returned an invalid payload — page rendered whole")
    except Exception as exc:
        logger.warning("gpt-5 layout classification failed: %s — page rendered whole", exc)
    return None


def _resolve_ai_gutter(layout: PageLayout, classification: dict | None) -> float | None:
    """Turn an AI classification into a render decision for one ambiguous page.

    Prefer the deterministic candidate gutter (a measured whitespace column — pixel-accurate,
    guaranteed not to cut glyphs); the AI's gutter_x is used only when no candidate exists
    and it lands in a plausible range. No usable coordinate → whole (never guess 0.5)."""
    if classification is None or classification["layout"] != "two_column":
        return None
    if layout.verdict.gutter_frac is not None:
        return layout.verdict.gutter_frac
    gutter_x = classification.get("gutter_x")
    if gutter_x is not None:
        frac = gutter_x / 1000.0
        if _AI_GUTTER_RANGE[0] <= frac <= _AI_GUTTER_RANGE[1]:
            return frac
    return None


async def resolve_pdf_layouts(
    file_bytes: bytes, page_indices: list[int], *, audit_ctx: dict | None = None
) -> tuple[list[tuple[int, float | None]], dict]:
    """Hybrid per-page layout resolution: deterministic verdicts first, then the AI
    classifier for ambiguous pages only.

    Returns (decisions, stats):
    - decisions = [(page_index, gutter_frac | None)] in page order (None = whole page),
      ready for `page_layout.render_regions_for_decisions`.
    - stats = {"pages", "split", "whole", "ambiguous", "ai_split", "ai_whole",
               "ai_fallback_whole", "mini_404"} for logging / job output.
    """
    layouts = await asyncio.to_thread(analyze_pdf_layouts, file_bytes, page_indices)

    stats = {
        "pages": len(layouts),
        "split": sum(1 for lay in layouts if lay.verdict.decision == "split"),
        "whole": sum(1 for lay in layouts if lay.verdict.decision == "whole"),
        "ambiguous": sum(1 for lay in layouts if lay.verdict.decision == "ambiguous"),
        "ai_split": 0, "ai_whole": 0, "ai_fallback_whole": 0, "mini_404": False,
    }

    ambiguous = [lay for lay in layouts if lay.verdict.decision == "ambiguous"]
    resolved: dict[int, float | None] = {}
    if ambiguous:
        state = {"mini_supports_images": True}   # per-job memo — created once per call
        sem = asyncio.Semaphore(CLASSIFY_CONCURRENCY)

        async def _one(lay: PageLayout) -> None:
            async with sem:
                classification = None
                if lay.analysis_png is not None:
                    classification = await classify_page_layout(
                        lay.analysis_png, state=state, audit_ctx=audit_ctx
                    )
                gutter = _resolve_ai_gutter(lay, classification)
                resolved[lay.page_index] = gutter
                if classification is None:
                    stats["ai_fallback_whole"] += 1
                elif gutter is not None:
                    stats["ai_split"] += 1
                else:
                    stats["ai_whole"] += 1

        await asyncio.gather(*[_one(lay) for lay in ambiguous])
        stats["mini_404"] = not state["mini_supports_images"]

    decisions: list[tuple[int, float | None]] = []
    for lay in layouts:
        if lay.verdict.decision == "split":
            decisions.append((lay.page_index, lay.verdict.gutter_frac))
        elif lay.verdict.decision == "ambiguous":
            decisions.append((lay.page_index, resolved.get(lay.page_index)))
        else:
            decisions.append((lay.page_index, None))

    if stats["ambiguous"]:
        logger.info(
            "Hybrid layout: %d page(s) — %d split / %d whole (deterministic), "
            "%d ambiguous → AI: %d split, %d whole, %d unavailable→whole%s. Signals: %s",
            stats["pages"], stats["split"], stats["whole"], stats["ambiguous"],
            stats["ai_split"], stats["ai_whole"], stats["ai_fallback_whole"],
            " (gpt-5-mini 404 → gpt-5)" if stats["mini_404"] else "",
            {lay.page_index + 1: lay.verdict.signals for lay in ambiguous},
        )
    else:
        logger.info(
            "Hybrid layout: %d page(s) — %d split / %d whole, all deterministic",
            stats["pages"], stats["split"], stats["whole"],
        )
    return decisions, stats


def layout_summary(stats: dict) -> str:
    """One Processing-Logs line, e.g. 'Layout: 12 page(s) — 3 split, 8 whole, 1 AI-classified'."""
    line = (
        f"Layout: {stats['pages']} page(s) — {stats['split'] + stats['ai_split']} split, "
        f"{stats['pages'] - stats['split'] - stats['ai_split']} whole"
    )
    if stats["ambiguous"]:
        line += f", {stats['ambiguous']} AI-classified"
    return line
