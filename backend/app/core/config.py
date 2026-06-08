from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).parent.parent.parent / ".env"  # backend/.env


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore")

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://neurafix:neurafix_dev_pass@localhost:5432/neurafix"

    # Auth
    JWT_SECRET: str = "change-this-secret"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Cloudflare R2
    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET_NAME: str = "neurafix-files"
    R2_PUBLIC_OR_ENDPOINT_URL: str = ""

    # Pinecone
    PINECONE_API_KEY: str = ""
    PINECONE_INDEX_NAME: str = "neurafix"
    PINECONE_INDEX_HOST: str = ""
    PINECONE_ENVIRONMENT: str = ""

    # AI Models — Azure OpenAI
    AZURE_OPENAI_ENDPOINT: str = ""
    AZURE_OPENAI_API_KEY: str = ""
    AZURE_OPENAI_API_VERSION_REASONING: str = "2026-04-24"
    AZURE_OPENAI_API_VERSION_EMBEDDING: str = "2025-01-01-preview"
    AZURE_OPENAI_API_VERSION_TRANSCRIPTION: str = "2025-03-01-preview"
    MODEL_REASONING: str = "gpt-5.5"
    MODEL_EMBEDDING: str = "text-embedding-3-large"
    MODEL_TRANSCRIPTION: str = "whisper"
    EMBEDDING_DIMENSIONS: int = 3072

    # AI Models — Google Gemini (VISION ONLY: handwriting extraction, structure pass,
    # annotation locator). Reasoning/embeddings/transcription stay on Azure OpenAI.
    GEMINI_API_KEY: str = ""
    MODEL_VISION: str = ""

    # AI / worker timeouts (seconds)
    AI_REQUEST_TIMEOUT_SECONDS: int = 180   # per Azure OpenAI call
    GEMINI_REQUEST_TIMEOUT_SECONDS: int = 180  # per Gemini vision call
    AI_MAX_RETRIES: int = 3                  # transient-error retries per AI call
    # Gemini free tier is rate-limited PER MINUTE, so on a rate-limit error we wait a
    # full minute before retrying (a short exponential backoff would just hit it again).
    GEMINI_RATE_LIMIT_RETRY_SECONDS: int = 60
    GEMINI_RATE_LIMIT_MAX_RETRIES: int = 3
    TASK_TIMEOUT_SECONDS: int = 1800         # hard ceiling for a single Celery job

    # URLs
    FRONTEND_URL: str = "http://localhost:5173"
    BACKEND_URL: str = "http://localhost:8000"

    # Seed defaults
    DEFAULT_ADMIN_EMAIL: str = "admin@neurafix.ai"
    DEFAULT_ADMIN_PASSWORD: str = "Admin@123"
    DEFAULT_ADMIN_NAME: str = "Institute Admin"

    def missing_required(self) -> list[str]:
        """Return names of critical settings still left at an empty/placeholder
        value. Used at startup to fail fast with a readable message instead of
        crashing deep inside a request or background task."""
        checks: dict[str, bool] = {
            "DATABASE_URL": bool(self.DATABASE_URL) and "@localhost" not in self.DATABASE_URL,
            "JWT_SECRET": bool(self.JWT_SECRET) and self.JWT_SECRET != "change-this-secret",
            "REDIS_URL": bool(self.REDIS_URL) and self.REDIS_URL != "redis://localhost:6379/0",
            "AZURE_OPENAI_ENDPOINT": bool(self.AZURE_OPENAI_ENDPOINT),
            "AZURE_OPENAI_API_KEY": bool(self.AZURE_OPENAI_API_KEY),
            "PINECONE_API_KEY": bool(self.PINECONE_API_KEY),
            "PINECONE_INDEX_HOST": bool(self.PINECONE_INDEX_HOST),
            "R2_ACCOUNT_ID": bool(self.R2_ACCOUNT_ID),
            "R2_ACCESS_KEY_ID": bool(self.R2_ACCESS_KEY_ID),
            "R2_SECRET_ACCESS_KEY": bool(self.R2_SECRET_ACCESS_KEY),
        }
        return [name for name, ok in checks.items() if not ok]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
