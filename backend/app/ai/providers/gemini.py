import asyncio
import json
import logging

from google import genai
from google.genai import types

from app.core.config import settings
from app.ai.providers.base import AIModelProvider

logger = logging.getLogger(__name__)


def _get_client() -> genai.Client:
    if not settings.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    return genai.Client(api_key=settings.GEMINI_API_KEY)


class GeminiProvider(AIModelProvider):
    async def generate_text(self, prompt: str, schema: dict | None = None) -> dict:
        def _sync() -> dict:
            client = _get_client()
            config: dict = {}
            if schema is not None:
                config["response_mime_type"] = "application/json"

            response = client.models.generate_content(
                model=settings.DEFAULT_TEXT_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(**config) if config else None,
            )
            text = response.text.strip()

            if schema is not None:
                if text.startswith("```"):
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                return json.loads(text)
            return {"text": text}

        return await asyncio.to_thread(_sync)

    async def generate_with_file(
        self,
        prompt: str,
        file_bytes: bytes,
        mime_type: str,
        schema: dict | None = None,
    ) -> dict:
        def _sync() -> dict:
            client = _get_client()
            config: dict = {}
            if schema is not None:
                config["response_mime_type"] = "application/json"

            part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
            response = client.models.generate_content(
                model=settings.DEFAULT_VISION_MODEL,
                contents=[prompt, part],
                config=types.GenerateContentConfig(**config) if config else None,
            )
            text = response.text.strip()

            if schema is not None:
                if text.startswith("```"):
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                return json.loads(text)
            return {"text": text}

        return await asyncio.to_thread(_sync)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        def _sync() -> list[list[float]]:
            client = _get_client()
            model_name = settings.DEFAULT_EMBEDDING_MODEL
            embeddings: list[list[float]] = []

            batch_size = 100
            for i in range(0, len(texts), batch_size):
                batch = texts[i : i + batch_size]
                result = client.models.embed_content(
                    model=model_name,
                    contents=batch,
                )
                for emb in result.embeddings:
                    embeddings.append(emb.values)

            return embeddings

        return await asyncio.to_thread(_sync)

    async def transcribe(self, audio_bytes: bytes, mime_type: str) -> dict:
        prompt = (
            "Transcribe this audio accurately. Return JSON with: "
            '"text" (full transcript) and "segments" '
            "(list of {start_seconds, end_seconds, text})."
        )
        return await self.generate_with_file(
            prompt, audio_bytes, mime_type, schema={"type": "object"}
        )
