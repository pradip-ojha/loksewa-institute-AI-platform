import asyncio
import base64
import io
import json
import logging
import random
import time

from openai import (
    AsyncAzureOpenAI,
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)

from app.core.config import settings
from app.core.exceptions import AIResponseError, ExternalServiceError
from app.ai.providers.base import AIModelProvider

logger = logging.getLogger(__name__)

# One AsyncAzureOpenAI client per api-version (the SDK pins api_version at client
# construction, and the tiered deployments may use different api-versions). Clients
# are cheap to hold open and safe to share across the worker's single event loop.
_clients: dict[str, AsyncAzureOpenAI] = {}

# Errors worth retrying: the call did not complete but may succeed if repeated.
# A malformed JSON body is NOT here — that call completed, the content is just unusable.
_TRANSIENT_ERRORS = (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError)


def _get_client(api_version: str) -> AsyncAzureOpenAI:
    client = _clients.get(api_version)
    if client is None:
        if not settings.AZURE_OPENAI_ENDPOINT or not settings.AZURE_OPENAI_API_KEY:
            raise RuntimeError("AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY must be configured.")
        client = AsyncAzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=api_version,
        )
        _clients[api_version] = client
    return client


def _strip_code_fences(text: str) -> str:
    """Models sometimes wrap JSON in ```json ... ``` fences despite json_object mode."""
    t = text.strip()
    if t.startswith("```"):
        first_newline = t.find("\n")
        if first_newline != -1:
            t = t[first_newline + 1 :]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[: t.rstrip().rfind("```")]
    return t.strip()


def _response_text(response) -> str:
    choices = getattr(response, "choices", None)
    if not choices:
        raise AIResponseError("model returned no choices")
    choice = choices[0]
    finish_reason = getattr(choice, "finish_reason", None)
    content = getattr(choice.message, "content", None)

    # Diagnose null/empty content before attempting JSON parse.
    if not content or not content.strip():
        refusal = getattr(choice.message, "refusal", None)
        if finish_reason == "content_filter":
            raise AIResponseError("Azure content filter blocked the response for this image")
        if refusal:
            raise AIResponseError(f"model refused to respond: {str(refusal)[:200]}")
        if finish_reason == "length":
            # Reasoning models consume thinking tokens from max_completion_tokens, leaving
            # nothing for output. Unset MCQ_VISION_MAX_TOKENS (set to 0) or raise it.
            raise AIResponseError(
                "output truncated at max_completion_tokens — model produced no content. "
                "Set MCQ_VISION_MAX_TOKENS=0 in .env to remove the cap, or raise it to 16384+"
            )
        raise AIResponseError(
            f"model returned empty content (finish_reason={finish_reason!r}); "
            "if this recurs, check Azure deployment logs for quota or availability issues"
        )

    if finish_reason == "length":
        # Got some content but it was cut off. In json_object mode Azure forces the JSON
        # closed by collapsing open arrays — the response may silently lose questions.
        # Raising here is better than silently returning truncated data.
        raise AIResponseError(
            "output truncated at max_completion_tokens — raise MCQ_VISION_MAX_TOKENS "
            "or split the page into smaller regions before re-uploading"
        )
    return content.strip()


def _parse_json(text: str, *, agent_type: str | None, task_type: str | None) -> dict:
    cleaned = _strip_code_fences(text)
    if not cleaned:
        raise AIResponseError("model returned empty content")
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.error(
            "AI returned invalid JSON (agent=%s task=%s): %s",
            agent_type, task_type, cleaned[:300],
        )
        raise AIResponseError(f"invalid JSON in model response: {exc}") from exc
    if not isinstance(data, dict):
        raise AIResponseError("model JSON was not an object")
    return data


def _retry_after_seconds(exc: Exception) -> float | None:
    """Server-suggested wait from a 429, if present. The SDK surfaces the response on
    the error; honor its `retry-after` (seconds) / `retry-after-ms` header so we don't
    hammer back inside the same throttle window."""
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None) or {}
    try:
        if headers.get("retry-after-ms"):
            return float(headers["retry-after-ms"]) / 1000.0
        if headers.get("retry-after"):
            return float(headers["retry-after"])
    except (TypeError, ValueError):
        return None
    return None


async def _call_with_retry(make_call, *, op: str):
    """Run an async Azure SDK call (returned fresh by ``make_call`` each attempt)
    with a bounded retry on transient errors (rate-limit / timeout / connection /
    5xx). ``make_call`` is a zero-arg callable returning a coroutine — it is
    re-invoked per attempt so a fresh request is issued each time.

    On a 429 the server's ``retry-after`` is honored (so we wait out the actual throttle
    window instead of three quick exponential retries that all land inside it); other
    transient errors use capped exponential backoff. Both add jitter so concurrent
    callers (the Semaphore-6 fan-out) don't retry in lockstep."""
    attempts = max(1, settings.AI_MAX_RETRIES)
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return await make_call()
        except _TRANSIENT_ERRORS as exc:
            last_exc = exc
            if attempt == attempts - 1:
                break
            server_delay = _retry_after_seconds(exc) if isinstance(exc, RateLimitError) else None
            base = server_delay if server_delay is not None else min(2 ** attempt, 10)
            backoff = base + random.uniform(0, 1)
            logger.warning("Azure OpenAI transient error on %s (attempt %d/%d): %s — retrying in %.1fs",
                           op, attempt + 1, attempts, exc, backoff)
            await asyncio.sleep(backoff)
    raise ExternalServiceError("azure_openai", f"{op} failed after {attempts} attempts: {last_exc}")


async def _create_with_retry(client: AsyncAzureOpenAI, **kwargs):
    """chat.completions.create with a hard per-call timeout and bounded retry on
    transient errors (rate-limit / timeout / connection / 5xx)."""
    return await _call_with_retry(
        lambda: client.chat.completions.create(
            timeout=settings.AI_REQUEST_TIMEOUT_SECONDS,
            **kwargs,
        ),
        op="chat.completions",
    )


async def _audit(audit_ctx: dict | None, *, model: str, api_version: str, input_tokens: int | None, output_tokens: int | None, latency_ms: int, status: str = "success", error_message: str | None = None) -> None:
    """Write one AI-request audit row on its OWN short-lived session.

    Deliberately does NOT write through `audit_ctx["db"]`. The caller's session is
    typically held open across the multi-minute model call; reusing it here would
    (a) ride a connection that the managed DB may have dropped server-side during the
    long call, and (b) `commit()` the caller's transaction as a side effect of audit.
    A fresh session is validated on checkout (pool_pre_ping) and isolated, so audit
    integrity no longer depends on the AI call's duration. Best-effort: a failure is
    logged and never propagates to the model result."""
    if not audit_ctx:
        return
    try:
        from app.core.database import AsyncSessionLocal
        from app.modules.ai_audit.service import log_ai_request
        async with AsyncSessionLocal() as db:
            await log_ai_request(
                db,
                provider="azure_openai",
                model=model,
                api_version=api_version,
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
        logger.warning("AI audit logging failed: %s", exc)


class AzureOpenAIProvider(AIModelProvider):
    """Azure OpenAI chat/vision provider for a single deployment tier.

    The chat deployment (``model``) and its ``api_version`` are fixed per instance,
    selected by ``model_router.get_provider(task_type)``:
      - reasoning (gpt-5.5)        — default
      - thinking  (gpt-5)          — typed text/vision extraction
      - fast      (gpt-5-mini)     — semantic chunking
    ``embed()`` and ``transcribe()`` ignore the tier and use their dedicated
    embedding/transcription deployments + api-versions.
    """

    def __init__(self, model: str | None = None, api_version: str | None = None):
        self.model = model or settings.MODEL_REASONING
        self.api_version = api_version or settings.AZURE_OPENAI_API_VERSION_REASONING

    async def generate_text(self, prompt: str, schema: dict | None = None, audit_ctx: dict | None = None) -> dict:
        client = _get_client(self.api_version)
        kwargs: dict = {}
        if schema is not None:
            kwargs["response_format"] = {"type": "json_object"}

        t0 = time.monotonic()
        try:
            response = await _create_with_retry(
                client,
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                **kwargs,
            )
            latency = int((time.monotonic() - t0) * 1000)
            usage = response.usage
            await _audit(audit_ctx, model=self.model, api_version=self.api_version, input_tokens=usage.prompt_tokens if usage else None, output_tokens=usage.completion_tokens if usage else None, latency_ms=latency)
        except Exception as exc:
            latency = int((time.monotonic() - t0) * 1000)
            await _audit(audit_ctx, model=self.model, api_version=self.api_version, input_tokens=None, output_tokens=None, latency_ms=latency, status="error", error_message=str(exc))
            raise

        text = _response_text(response)
        if schema is not None:
            return _parse_json(text, agent_type=(audit_ctx or {}).get("agent_type"), task_type=(audit_ctx or {}).get("task_type"))
        return {"text": text}

    async def stream_text(self, prompt: str, audit_ctx: dict | None = None):
        """Stream plain-text deltas (no json_object mode — streaming + structured
        JSON aren't supported together). The stream-OPEN is retried on transient
        errors; once tokens flow, errors surface to the caller (a partial stream
        can't be safely re-issued). One audit row is logged at completion."""
        client = _get_client(self.api_version)
        t0 = time.monotonic()
        usage = None
        audited = False
        try:
            stream = await _call_with_retry(
                lambda: client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    stream=True,
                    stream_options={"include_usage": True},
                    timeout=settings.AI_REQUEST_TIMEOUT_SECONDS,
                ),
                op="chat.completions.stream",
            )
            async for chunk in stream:
                # The final usage-only chunk carries no choices.
                if getattr(chunk, "usage", None):
                    usage = chunk.usage
                choices = getattr(chunk, "choices", None)
                if not choices:
                    continue
                delta = choices[0].delta
                content = getattr(delta, "content", None)
                if content:
                    yield content
        except Exception as exc:
            latency = int((time.monotonic() - t0) * 1000)
            await _audit(audit_ctx, model=self.model, api_version=self.api_version, input_tokens=None, output_tokens=None, latency_ms=latency, status="error", error_message=str(exc))
            audited = True
            raise
        finally:
            # ALWAYS record one audit row — including the client-disconnect case, where a
            # GeneratorExit (BaseException) bypasses `except Exception` and previously left
            # no audit at all. Awaiting here is safe because _audit never yields. `usage`
            # holds the billed tokens if the final usage chunk arrived before the cut.
            if not audited:
                latency = int((time.monotonic() - t0) * 1000)
                await _audit(
                    audit_ctx, model=self.model, api_version=self.api_version,
                    input_tokens=usage.prompt_tokens if usage else None,
                    output_tokens=usage.completion_tokens if usage else None,
                    latency_ms=latency,
                )

    async def generate_with_file(self, prompt: str, file_bytes: bytes, mime_type: str, schema: dict | None = None, audit_ctx: dict | None = None) -> dict:
        client = _get_client(self.api_version)
        kwargs: dict = {}
        if schema is not None:
            kwargs["response_format"] = {"type": "json_object"}

        if mime_type.startswith("image/"):
            b64 = base64.b64encode(file_bytes).decode()
            content: list | str = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
            ]
        else:
            content = prompt

        t0 = time.monotonic()
        try:
            response = await _create_with_retry(
                client,
                model=self.model,
                messages=[{"role": "user", "content": content}],
                **kwargs,
            )
            latency = int((time.monotonic() - t0) * 1000)
            usage = response.usage
            await _audit(audit_ctx, model=self.model, api_version=self.api_version, input_tokens=usage.prompt_tokens if usage else None, output_tokens=usage.completion_tokens if usage else None, latency_ms=latency)
        except Exception as exc:
            latency = int((time.monotonic() - t0) * 1000)
            await _audit(audit_ctx, model=self.model, api_version=self.api_version, input_tokens=None, output_tokens=None, latency_ms=latency, status="error", error_message=str(exc))
            raise

        text = _response_text(response)
        if schema is not None:
            return _parse_json(text, agent_type=(audit_ctx or {}).get("agent_type"), task_type=(audit_ctx or {}).get("task_type"))
        return {"text": text}

    async def generate_with_image(self, prompt: str, image_bytes: bytes, schema: dict | None = None, audit_ctx: dict | None = None, max_tokens: int | None = None) -> dict:
        client = _get_client(self.api_version)
        b64 = base64.b64encode(image_bytes).decode()
        # Label the data URL by the actual bytes — PNG (lossless, used for OCR page/column
        # images) vs JPEG — so the API never mis-decodes a PNG sent under a jpeg label.
        mime = "image/png" if image_bytes[:8].startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg"
        kwargs: dict = {}
        if schema is not None:
            kwargs["response_format"] = {"type": "json_object"}
        if max_tokens:  # 0 or None → don't set; let Azure use the deployment default
            kwargs["max_completion_tokens"] = max_tokens

        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "high"}},
        ]

        t0 = time.monotonic()
        try:
            response = await _create_with_retry(
                client,
                model=self.model,
                messages=[{"role": "user", "content": content}],
                **kwargs,
            )
            latency = int((time.monotonic() - t0) * 1000)
            usage = response.usage
            await _audit(audit_ctx, model=self.model, api_version=self.api_version, input_tokens=usage.prompt_tokens if usage else None, output_tokens=usage.completion_tokens if usage else None, latency_ms=latency)
        except Exception as exc:
            latency = int((time.monotonic() - t0) * 1000)
            await _audit(audit_ctx, model=self.model, api_version=self.api_version, input_tokens=None, output_tokens=None, latency_ms=latency, status="error", error_message=str(exc))
            raise

        text = _response_text(response)
        if schema is not None:
            return _parse_json(text, agent_type=(audit_ctx or {}).get("agent_type"), task_type=(audit_ctx or {}).get("task_type"))
        return {"text": text}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        client = _get_client(settings.AZURE_OPENAI_API_VERSION_EMBEDDING)
        embeddings: list[list[float]] = []

        batch_size = 100
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            response = await _call_with_retry(
                lambda b=batch: client.embeddings.create(
                    model=settings.MODEL_EMBEDDING,
                    input=b,
                    dimensions=settings.EMBEDDING_DIMENSIONS,
                    timeout=settings.AI_REQUEST_TIMEOUT_SECONDS,
                ),
                op="embeddings",
            )
            for item in sorted(response.data, key=lambda x: x.index):
                embeddings.append(item.embedding)

        return embeddings

    async def transcribe(self, audio_bytes: bytes, mime_type: str, audit_ctx: dict | None = None) -> dict:
        if not settings.MODEL_TRANSCRIPTION:
            raise RuntimeError("MODEL_TRANSCRIPTION is not configured.")
        client = _get_client(settings.AZURE_OPENAI_API_VERSION_TRANSCRIPTION)
        # The transcription API infers the codec from the filename extension, so
        # the extension MUST name a real audio format. Our pipeline produces MP3
        # with mime "audio/mpeg" — sending it as "audio.mpeg" makes the API treat
        # it as an MPEG *video* container and reject it ("corrupted or unsupported").
        _ext_by_mime = {
            "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/mpga": "mp3",
            "audio/mp4": "mp4", "audio/m4a": "m4a", "audio/x-m4a": "m4a",
            "audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav",
            "audio/webm": "webm", "audio/ogg": "ogg", "audio/flac": "flac",
        }
        sub = (mime_type or "").split("/")[-1].lower()
        ext = _ext_by_mime.get((mime_type or "").lower()) or ("mp3" if sub in ("", "mpeg", "mpga") else sub)

        def _make_audio_file():
            # A fresh stream per attempt — a consumed BytesIO can't be re-read on retry.
            f = io.BytesIO(audio_bytes)
            f.name = f"audio.{ext}"
            return f

        # gpt-4o-transcribe / gpt-4o-mini-transcribe only support "json" or "text"
        # (Azure rejects "verbose_json" — and thus segment-level timestamps — for them).
        # The timeline is built by VideoTimelineAgent, which anchors each segment to the
        # GLOBAL time window of the audio chunk it came from; chunk size (audio_tools.
        # DEFAULT_CHUNK_MINUTES) is therefore the timeline's time granularity. Request "json".
        t0 = time.monotonic()
        try:
            response = await _call_with_retry(
                lambda: client.audio.transcriptions.create(
                    model=settings.MODEL_TRANSCRIPTION,
                    file=_make_audio_file(),
                    response_format="json",
                    timeout=settings.AI_REQUEST_TIMEOUT_SECONDS,
                ),
                op="transcription",
            )
            latency = int((time.monotonic() - t0) * 1000)
            await _audit(audit_ctx, model=settings.MODEL_TRANSCRIPTION, api_version=settings.AZURE_OPENAI_API_VERSION_TRANSCRIPTION, input_tokens=None, output_tokens=None, latency_ms=latency)
        except Exception as exc:
            latency = int((time.monotonic() - t0) * 1000)
            await _audit(audit_ctx, model=settings.MODEL_TRANSCRIPTION, api_version=settings.AZURE_OPENAI_API_VERSION_TRANSCRIPTION, input_tokens=None, output_tokens=None, latency_ms=latency, status="error", error_message=str(exc))
            raise

        return {
            "text": response.text,
            "segments": [
                {"start_seconds": s.start, "end_seconds": s.end, "text": s.text}
                for s in (getattr(response, "segments", None) or [])
            ],
        }
