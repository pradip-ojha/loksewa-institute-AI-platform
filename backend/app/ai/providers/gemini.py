"""Google Gemini provider — VISION ONLY.

Gemini reads Nepali/Devanagari handwriting noticeably better than GPT-5.5, so the
answer-sheet *vision* steps (structure pass, question-level extraction, annotation
locator, and the vision-OCR fallbacks) route here via `get_provider("vision")`.
All reasoning, embeddings and transcription stay on Azure OpenAI.

Only the image methods are implemented; the text/embed/transcribe methods raise so
a mis-route fails loudly instead of silently using the wrong model. Calls are logged
to `ai_requests` as `provider="gemini"` exactly like the Azure provider, so the admin
debug endpoints surface them too.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time

from app.ai.providers.base import AIModelProvider
from app.core.config import settings
from app.core.exceptions import AIResponseError, ExternalServiceError

logger = logging.getLogger(__name__)

_client = None  # lazily-created google.genai.Client


def _get_client():
    global _client
    if _client is None:
        if not settings.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY must be configured to use the vision provider.")
        if not settings.MODEL_VISION:
            raise RuntimeError("MODEL_VISION must be configured to use the vision provider.")
        try:
            from google import genai  # google-genai SDK
        except ImportError as exc:  # pragma: no cover - dependency missing
            raise RuntimeError(
                "google-genai is not installed. Add `google-genai` to requirements and pip install."
            ) from exc
        _client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _client


def _strip_code_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        nl = t.find("\n")
        if nl != -1:
            t = t[nl + 1:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[: t.rstrip().rfind("```")]
    return t.strip()


def _parse_json(text: str, *, agent_type: str | None, task_type: str | None) -> dict:
    cleaned = _strip_code_fences(text)
    if not cleaned:
        raise AIResponseError("Gemini returned empty content")
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.error("Gemini returned invalid JSON (agent=%s task=%s): %s",
                     agent_type, task_type, cleaned[:300])
        raise AIResponseError(f"invalid JSON in Gemini response: {exc}") from exc
    if not isinstance(data, dict):
        raise AIResponseError("Gemini JSON was not an object")
    return data


async def _audit(audit_ctx: dict | None, *, model: str, input_tokens: int | None,
                 output_tokens: int | None, latency_ms: int, status: str = "success",
                 error_message: str | None = None) -> None:
    if not audit_ctx:
        return
    db = audit_ctx.get("db")
    if db is None:
        return
    try:
        from app.modules.ai_audit.service import log_ai_request
        await log_ai_request(
            db,
            provider="gemini",
            model=model,
            api_version=None,
            agent_type=audit_ctx.get("agent_type"),
            task_type=audit_ctx.get("task_type"),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            status=status,
            latency_ms=latency_ms,
            error_message=error_message,
            related_entity_type=audit_ctx.get("entity_type"),
            related_entity_id=audit_ctx.get("entity_id"),
            output_summary=audit_ctx.get("output_summary"),
        )
    except Exception as exc:
        logger.warning("Gemini audit logging failed: %s", exc)


def _classify_429(exc: Exception) -> tuple[str | None, float | None]:
    """Classify a possible quota/rate-limit error.

    Returns (kind, retry_delay_seconds):
      • "daily"   — per-DAY free-tier quota exhausted (won't recover until reset;
                    DO NOT retry — waiting wastes minutes and still fails).
      • "minute"  — per-minute / short-window rate limit (retry after a wait).
      • "other"   — a generic 429 with no clear window (treat like a rate limit).
      • None       — not a quota/rate-limit error.
    retry_delay is the server-suggested delay (RetryInfo) when present.
    """
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    s = str(exc).lower()
    is_429 = code == 429 or "429" in s or "resource_exhausted" in s or "quota" in s or "rate limit" in s
    if not is_429:
        return None, None

    retry_delay = None
    m = re.search(r"retrydelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", s)
    if m:
        try:
            retry_delay = float(m.group(1))
        except ValueError:
            retry_delay = None

    if "perday" in s or "requestsperday" in s or "per day" in s:
        return "daily", retry_delay
    if "perminute" in s or "requestsperminute" in s or "per minute" in s:
        return "minute", retry_delay
    return "other", retry_delay


async def _generate_content_with_retry(*, contents):
    """gemini generate_content with bounded, quota-aware retries.

    • per-DAY free-tier quota → fail fast with a clear message (no point waiting).
    • per-minute / generic 429 → wait (server retryDelay, or 60s) then retry.
    • other transient errors → short exponential backoff.
    """
    from google.genai import types

    client = _get_client()
    config = types.GenerateContentConfig(response_mime_type="application/json")
    transient_attempts = max(1, settings.AI_MAX_RETRIES)
    rl_budget = max(1, settings.GEMINI_RATE_LIMIT_MAX_RETRIES)
    rl_wait = settings.GEMINI_RATE_LIMIT_RETRY_SECONDS

    last_exc: Exception | None = None
    transient_used = 0
    rl_used = 0
    while True:
        try:
            return await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=settings.MODEL_VISION, contents=contents, config=config,
                ),
                timeout=settings.GEMINI_REQUEST_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # google-genai raises various server/timeout errors
            last_exc = exc
            kind, retry_delay = _classify_429(exc)
            if kind == "daily":
                raise ExternalServiceError(
                    "gemini",
                    f"daily free-tier quota exhausted for model '{settings.MODEL_VISION}'. "
                    "Switch MODEL_VISION to a model with a higher free daily limit "
                    "(e.g. gemini-2.5-flash / gemini-2.5-flash-lite) or enable billing.",
                ) from exc
            if kind is not None:
                if rl_used >= rl_budget:
                    break
                rl_used += 1
                wait = retry_delay if (retry_delay and retry_delay > 0) else rl_wait
                logger.warning("Gemini rate-limited (%s); waiting %ss before retry (%d/%d)",
                               kind, wait, rl_used, rl_budget)
                await asyncio.sleep(wait)
                continue
            transient_used += 1
            if transient_used >= transient_attempts:
                break
            backoff = min(2 ** transient_used, 10)
            logger.warning("Gemini transient error (attempt %d/%d): %s — retrying in %ss",
                           transient_used, transient_attempts, exc, backoff)
            await asyncio.sleep(backoff)
    raise ExternalServiceError("gemini", f"request failed: {last_exc}")


def _usage(response) -> tuple[int | None, int | None]:
    um = getattr(response, "usage_metadata", None)
    if not um:
        return None, None
    return getattr(um, "prompt_token_count", None), getattr(um, "candidates_token_count", None)


class GeminiProvider(AIModelProvider):
    async def _run(self, contents, schema: dict | None, audit_ctx: dict | None) -> dict:
        t0 = time.monotonic()
        try:
            response = await _generate_content_with_retry(contents=contents)
            latency = int((time.monotonic() - t0) * 1000)
            inp, out = _usage(response)
            await _audit(audit_ctx, model=settings.MODEL_VISION, input_tokens=inp,
                         output_tokens=out, latency_ms=latency)
        except Exception as exc:
            latency = int((time.monotonic() - t0) * 1000)
            await _audit(audit_ctx, model=settings.MODEL_VISION, input_tokens=None,
                         output_tokens=None, latency_ms=latency, status="error",
                         error_message=str(exc))
            raise

        text = (getattr(response, "text", None) or "").strip()
        if schema is not None:
            return _parse_json(text, agent_type=(audit_ctx or {}).get("agent_type"),
                               task_type=(audit_ctx or {}).get("task_type"))
        return {"text": text}

    async def _vision(self, prompt: str, image_bytes: bytes, mime_type: str,
                      schema: dict | None, audit_ctx: dict | None) -> dict:
        from google.genai import types

        contents = [
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type or "image/png"),
            prompt,
        ]
        return await self._run(contents, schema, audit_ctx)

    async def generate_with_image(self, prompt: str, image_bytes: bytes,
                                  schema: dict | None = None, audit_ctx: dict | None = None) -> dict:
        return await self._vision(prompt, image_bytes, "image/png", schema, audit_ctx)

    async def generate_with_images(self, prompt: str, images: list[bytes],
                                   schema: dict | None = None, audit_ctx: dict | None = None,
                                   mime_type: str = "image/png") -> dict:
        """Vision over MULTIPLE images in one call (e.g. the whole-sheet structure pass).
        Not part of the base interface — only the Gemini vision provider supports it."""
        from google.genai import types

        contents = [types.Part.from_bytes(data=b, mime_type=mime_type) for b in images]
        contents.append(prompt)
        return await self._run(contents, schema, audit_ctx)

    async def generate_with_file(self, prompt: str, file_bytes: bytes, mime_type: str,
                                 schema: dict | None = None, audit_ctx: dict | None = None) -> dict:
        if mime_type.startswith("image/"):
            return await self._vision(prompt, file_bytes, mime_type, schema, audit_ctx)
        raise NotImplementedError("GeminiProvider handles image inputs only (vision provider).")

    async def generate_text(self, prompt: str, schema: dict | None = None, audit_ctx: dict | None = None) -> dict:
        raise NotImplementedError("GeminiProvider is vision-only; use the Azure provider for text.")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("GeminiProvider is vision-only; use the Azure provider for embeddings.")

    async def transcribe(self, audio_bytes: bytes, mime_type: str, audit_ctx: dict | None = None) -> dict:
        raise NotImplementedError("GeminiProvider is vision-only; use the Azure provider for transcription.")
