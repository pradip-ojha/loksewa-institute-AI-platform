from functools import lru_cache

from app.ai.providers.base import AIModelProvider
from app.ai.providers.azure_openai import AzureOpenAIProvider


@lru_cache
def get_provider(task_type: str = "text") -> AIModelProvider:
    """Return the AI provider for the given task type.

    All tasks route to Azure OpenAI. task_type is reserved for future
    routing (e.g., a dedicated transcription deployment).
    """
    return AzureOpenAIProvider()
