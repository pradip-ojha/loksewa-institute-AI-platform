from functools import lru_cache

from app.ai.providers.base import AIModelProvider
from app.ai.providers.azure_openai import AzureOpenAIProvider
from app.ai.providers.gemini import GeminiProvider


@lru_cache
def get_provider(task_type: str = "text") -> AIModelProvider:
    """Return the AI provider for the given task type.

    - "vision" → Google Gemini (handwriting extraction, structure pass, annotation
      locator — Gemini reads Nepali/Devanagari handwriting better than GPT-5.5).
    - everything else → Azure OpenAI (reasoning, embeddings, transcription).
    """
    if task_type == "vision":
        return GeminiProvider()
    return AzureOpenAIProvider()
