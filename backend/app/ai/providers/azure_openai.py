import asyncio
import base64
import io
import json
import logging
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

_reasoning_client: AsyncAzureOpenAI | None = None
_embedding_client: AsyncAzureOpenAI | None = None

# Errors worth retrying: the call did not complete but may succeed if repeated.
# A malformed JSON body is NOT here — that call completed, the content is just unusable.
_TRANSIENT_ERRORS = (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError)


def _get_reasoning_client() -> AsyncAzureOpenAI:
    global _reasoning_client
    if _reasoning_client is None:
        if not settings.AZURE_OPENAI_ENDPOINT or not settings.AZURE_OPENAI_API_KEY:
            raise RuntimeError("AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY must be configured.")
        _reasoning_client = AsyncAzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION_REASONING,
        )
    return _reasoning_client


def _get_embedding_client() -> AsyncAzureOpenAI:
    global _embedding_client
    if _embedding_client is None:
        if not settings.AZURE_OPENAI_ENDPOINT or not settings.AZURE_OPENAI_API_KEY:
            raise RuntimeError("AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY must be configured.")
        _embedding_client = AsyncAzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION_EMBEDDING,
        )
    return _embedding_client


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
    content = choices[0].message.content
    return (content or "").strip()


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


async def _call_with_retry(make_call, *, op: str):
    """Run an async Azure SDK call (returned fresh by ``make_call`` each attempt)
    with a bounded retry on transient errors (rate-limit / timeout / connection /
    5xx). ``make_call`` is a zero-arg callable returning a coroutine — it is
    re-invoked per attempt so a fresh request is issued each time."""
    attempts = max(1, settings.AI_MAX_RETRIES)
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return await make_call()
        except _TRANSIENT_ERRORS as exc:
            last_exc = exc
            if attempt == attempts - 1:
                break
            backoff = min(2 ** attempt, 10)
            logger.warning("Azure OpenAI transient error on %s (attempt %d/%d): %s — retrying in %ss",
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
    if not audit_ctx:
        return
    db = audit_ctx.get("db")
    if db is None:
        return
    try:
        from app.modules.ai_audit.service import log_ai_request
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
    async def generate_text(self, prompt: str, schema: dict | None = None, audit_ctx: dict | None = None) -> dict:
        client = _get_reasoning_client()
        kwargs: dict = {}
        if schema is not None:
            kwargs["response_format"] = {"type": "json_object"}

        t0 = time.monotonic()
        try:
            response = await _create_with_retry(
                client,
                model=settings.MODEL_REASONING,
                messages=[{"role": "user", "content": prompt}],
                **kwargs,
            )
            latency = int((time.monotonic() - t0) * 1000)
            usage = response.usage
            await _audit(audit_ctx, model=settings.MODEL_REASONING, api_version=settings.AZURE_OPENAI_API_VERSION_REASONING, input_tokens=usage.prompt_tokens if usage else None, output_tokens=usage.completion_tokens if usage else None, latency_ms=latency)
        except Exception as exc:
            latency = int((time.monotonic() - t0) * 1000)
            await _audit(audit_ctx, model=settings.MODEL_REASONING, api_version=settings.AZURE_OPENAI_API_VERSION_REASONING, input_tokens=None, output_tokens=None, latency_ms=latency, status="error", error_message=str(exc))
            raise

        text = _response_text(response)
        if schema is not None:
            return _parse_json(text, agent_type=(audit_ctx or {}).get("agent_type"), task_type=(audit_ctx or {}).get("task_type"))
        return {"text": text}

    async def generate_with_file(self, prompt: str, file_bytes: bytes, mime_type: str, schema: dict | None = None, audit_ctx: dict | None = None) -> dict:
        client = _get_reasoning_client()
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
                model=settings.MODEL_REASONING,
                messages=[{"role": "user", "content": content}],
                **kwargs,
            )
            latency = int((time.monotonic() - t0) * 1000)
            usage = response.usage
            await _audit(audit_ctx, model=settings.MODEL_REASONING, api_version=settings.AZURE_OPENAI_API_VERSION_REASONING, input_tokens=usage.prompt_tokens if usage else None, output_tokens=usage.completion_tokens if usage else None, latency_ms=latency)
        except Exception as exc:
            latency = int((time.monotonic() - t0) * 1000)
            await _audit(audit_ctx, model=settings.MODEL_REASONING, api_version=settings.AZURE_OPENAI_API_VERSION_REASONING, input_tokens=None, output_tokens=None, latency_ms=latency, status="error", error_message=str(exc))
            raise

        text = _response_text(response)
        if schema is not None:
            return _parse_json(text, agent_type=(audit_ctx or {}).get("agent_type"), task_type=(audit_ctx or {}).get("task_type"))
        return {"text": text}

    async def generate_with_image(self, prompt: str, image_bytes: bytes, schema: dict | None = None, audit_ctx: dict | None = None) -> dict:
        client = _get_reasoning_client()
        b64 = base64.b64encode(image_bytes).decode()
        kwargs: dict = {}
        if schema is not None:
            kwargs["response_format"] = {"type": "json_object"}

        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}},
        ]

        t0 = time.monotonic()
        try:
            response = await _create_with_retry(
                client,
                model=settings.MODEL_REASONING,
                messages=[{"role": "user", "content": content}],
                **kwargs,
            )
            latency = int((time.monotonic() - t0) * 1000)
            usage = response.usage
            await _audit(audit_ctx, model=settings.MODEL_REASONING, api_version=settings.AZURE_OPENAI_API_VERSION_REASONING, input_tokens=usage.prompt_tokens if usage else None, output_tokens=usage.completion_tokens if usage else None, latency_ms=latency)
        except Exception as exc:
            latency = int((time.monotonic() - t0) * 1000)
            await _audit(audit_ctx, model=settings.MODEL_REASONING, api_version=settings.AZURE_OPENAI_API_VERSION_REASONING, input_tokens=None, output_tokens=None, latency_ms=latency, status="error", error_message=str(exc))
            raise

        text = _response_text(response)
        if schema is not None:
            return _parse_json(text, agent_type=(audit_ctx or {}).get("agent_type"), task_type=(audit_ctx or {}).get("task_type"))
        return {"text": text}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        client = _get_embedding_client()
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
        client = _get_reasoning_client()
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
        # (verbose_json — and thus segment-level timestamps — is whisper-1 only).
        # Our timeline is built by VideoTimelineAgent from the cleaned transcript,
        # so we don't depend on transcription segments; request plain "json".
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
