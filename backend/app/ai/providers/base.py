from abc import ABC, abstractmethod
from typing import Any, AsyncIterator


class AIModelProvider(ABC):
    @abstractmethod
    async def generate_text(self, prompt: str, schema: dict | None = None, audit_ctx: dict | None = None) -> dict:
        """Generate structured JSON from text prompt. audit_ctx: {db, agent_type, task_type, entity_type, entity_id}"""

    async def stream_text(self, prompt: str, audit_ctx: dict | None = None) -> AsyncIterator[str]:
        """Stream plain-text deltas from a text prompt (no structured-JSON mode).

        Not every provider supports streaming (Gemini here is vision-only), so the
        base raises and streaming-capable providers override. Callers should only
        invoke this on a provider tier known to support it (the Azure chat tiers)."""
        raise NotImplementedError(f"{type(self).__name__} does not support streaming")
        yield  # pragma: no cover — makes this an async generator

    @abstractmethod
    async def generate_with_file(self, prompt: str, file_bytes: bytes, mime_type: str, schema: dict | None = None, audit_ctx: dict | None = None) -> dict:
        """Generate structured JSON from prompt + file content."""

    @abstractmethod
    async def generate_with_image(self, prompt: str, image_bytes: bytes, schema: dict | None = None, audit_ctx: dict | None = None, max_tokens: int | None = None) -> dict:
        """Generate structured JSON from prompt + image bytes."""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text."""

    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, mime_type: str, audit_ctx: dict | None = None) -> dict:
        """Transcribe audio to text with timestamps."""
