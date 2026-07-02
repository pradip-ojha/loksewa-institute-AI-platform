"""Shared helper for streaming a tutor answer followed by a small metadata tail.

Streaming and structured-JSON output can't be used together, so the streaming
tutor prompts instruct the model to emit the markdown answer FIRST, then the exact
marker ``<<<META>>>`` on a new line, then a one-line JSON object with
``follow_up_suggestions`` / ``language`` / ``confidence``.

``stream_answer_with_meta`` streams every delta BEFORE the marker to the caller as
answer text, buffers everything after it, and parses that tail into ``meta_sink``
(fail-open — a malformed tail just yields empty metadata, the answer still stands).
The marker may arrive split across token boundaries, so we hold back any buffer
suffix that could be a partial marker prefix before emitting.
"""
import json
import logging
from typing import AsyncIterator

logger = logging.getLogger(__name__)

META_SENTINEL = "<<<META>>>"


async def stream_answer_with_meta(provider, prompt: str, audit_ctx: dict, meta_sink: dict) -> AsyncIterator[str]:
    buffer = ""           # text not yet emitted (may contain a partial sentinel suffix)
    tail = ""             # everything after the sentinel
    seen_sentinel = False
    async for delta in provider.stream_text(prompt, audit_ctx=audit_ctx):
        if seen_sentinel:
            tail += delta
            continue
        buffer += delta
        idx = buffer.find(META_SENTINEL)
        if idx != -1:
            answer_part = buffer[:idx]
            if answer_part:
                yield answer_part
            tail = buffer[idx + len(META_SENTINEL):]
            seen_sentinel = True
            buffer = ""
            continue
        # Hold back any suffix that could be the start of the sentinel; emit the rest.
        safe = _safe_emit_len(buffer, META_SENTINEL)
        if safe > 0:
            yield buffer[:safe]
            buffer = buffer[safe:]
    if not seen_sentinel and buffer:
        yield buffer
    meta_sink.update(_parse_meta(tail))


def _safe_emit_len(buffer: str, sentinel: str) -> int:
    """How many leading chars of ``buffer`` are safe to emit now — i.e. cannot be
    part of a sentinel only partially received at the buffer's end."""
    max_hold = min(len(buffer), len(sentinel) - 1)
    for hold in range(max_hold, 0, -1):
        if sentinel.startswith(buffer[-hold:]):
            return len(buffer) - hold
    return len(buffer)


def _parse_meta(tail: str) -> dict:
    t = tail.strip()
    if t.startswith("```"):
        nl = t.find("\n")
        if nl != -1:
            t = t[nl + 1:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[: t.rstrip().rfind("```")]
        t = t.strip()
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return _normalize({})
    try:
        data = json.loads(t[start:end + 1])
    except Exception as exc:  # noqa: BLE001 — fail open
        logger.warning("tutor meta tail parse failed (fail-open): %s", exc)
        return _normalize({})
    return _normalize(data if isinstance(data, dict) else {})


def _normalize(data: dict) -> dict:
    fu = data.get("follow_up_suggestions")
    try:
        confidence = float(data.get("confidence", 0) or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "follow_up_suggestions": [str(s) for s in fu][:4] if isinstance(fu, list) else [],
        "language": str(data.get("language") or "nepali"),
        "confidence": confidence,
    }
