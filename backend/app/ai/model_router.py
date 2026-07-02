from functools import lru_cache

from app.core.config import settings
from app.ai.providers.base import AIModelProvider
from app.ai.providers.azure_openai import AzureOpenAIProvider
from app.ai.providers.gemini import GeminiProvider


@lru_cache
def get_provider(task_type: str = "text") -> AIModelProvider:
    """Return the AI provider (and Azure deployment tier) for the given task type.

    Governing principle: typed text (even scanned/printed images) → Azure OpenAI;
    handwritten Nepali/Devanagari → Gemini. Model tiers:

    - "vision" / "vision_handwritten" → Gemini (MODEL_VISION) — handwritten answer-sheet
      structure pass, handwriting extraction, annotation locator ONLY.
    - "thinking" / "text_extraction" / "vision_typed" / "consistency_check" → Azure gpt-5
      (thinking) — the general gpt-5 tier ("thinking" is the clear name; the others are
      kept as aliases). Covers typed text/vision extraction (existing-MCQ docs, content-PDF
      text, scanned knowledge-PDF OCR, question-paper / model-answer / rubric extraction),
      the skill-EVALUATION consistency pass (it only flags serious structural issues — wrong
      mapping, qnum/marks mismatch, breakdown ≠ full marks — so gpt-5 is enough; the gpt-5.5
      reasoning tier still does the actual skill GENERATION/regeneration), AND the simpler
      routing/cleaning/labeling agents that don't need reasoning-tier judgement.
    - "chunking" → configurable via settings.CHUNKING_MODEL_TIER (".env"): default
      "thinking" = Azure gpt-5 (chunking is a one-time per-document cost whose quality
      underpins all retrieval), or "fast" = Azure gpt-5-mini for the cheaper option.
    - "routing" → Azure gpt-5-mini (fast) — cheap routing/selection decisions (e.g. which
      question's context a feedback chat needs).
    - everything else ("reasoning", "text", …) → Azure gpt-5.5 (reasoning) — MCQ
      generation, checking-skill generation, answer evaluation, reviewer pass, tutors,
      chatbots — plus embeddings/transcription (which use their own dedicated deployments).
    """
    if task_type in ("vision", "vision_handwritten"):
        return GeminiProvider()
    if task_type in ("thinking", "text_extraction", "vision_typed", "consistency_check"):
        return AzureOpenAIProvider(
            model=settings.MODEL_CHAT_THINKING,
            api_version=settings.AZURE_OPENAI_API_VERSION_THINKING,
        )
    if task_type == "chunking":
        # Togglable from .env: default gpt-5 ("thinking"), or gpt-5-mini ("fast").
        if settings.CHUNKING_MODEL_TIER.strip().lower() == "fast":
            return AzureOpenAIProvider(
                model=settings.MODEL_CHAT_FAST,
                api_version=settings.AZURE_OPENAI_API_VERSION_FAST,
            )
        return AzureOpenAIProvider(
            model=settings.MODEL_CHAT_THINKING,
            api_version=settings.AZURE_OPENAI_API_VERSION_THINKING,
        )
    if task_type == "routing":
        return AzureOpenAIProvider(
            model=settings.MODEL_CHAT_FAST,
            api_version=settings.AZURE_OPENAI_API_VERSION_FAST,
        )
    return AzureOpenAIProvider()
