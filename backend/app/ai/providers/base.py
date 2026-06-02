from abc import ABC, abstractmethod
from typing import Any


class AIModelProvider(ABC):
    @abstractmethod
    async def generate_text(self, prompt: str, schema: dict | None = None, audit_ctx: dict | None = None) -> dict:
        """Generate structured JSON from text prompt. audit_ctx: {db, agent_type, task_type, entity_type, entity_id}"""

    @abstractmethod
    async def generate_with_file(self, prompt: str, file_bytes: bytes, mime_type: str, schema: dict | None = None, audit_ctx: dict | None = None) -> dict:
        """Generate structured JSON from prompt + file content."""

    @abstractmethod
    async def generate_with_image(self, prompt: str, image_bytes: bytes, schema: dict | None = None, audit_ctx: dict | None = None) -> dict:
        """Generate structured JSON from prompt + image bytes."""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text."""

    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, mime_type: str, audit_ctx: dict | None = None) -> dict:
        """Transcribe audio to text with timestamps."""
