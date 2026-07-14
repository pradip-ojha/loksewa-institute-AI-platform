from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).parent.parent.parent / ".env"  # backend/.env

_DEFAULT_DATABASE_URL = "postgresql+asyncpg://neurafix:neurafix_dev_pass@localhost:5432/neurafix"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore")

    # Deployment environment. "production" makes startup FAIL CLOSED on insecure config
    # (placeholder JWT secret, weak default admin password) instead of merely logging —
    # so the app can never run publicly on a known secret. Default "development" keeps
    # local runs convenient.
    ENVIRONMENT: str = "development"

    # TEMPORARY DEBUG: when true, the knowledge ingestion pipeline dumps each stage's
    # output (raw OCR text, sections, raw extraction JSON, final chunks) to
    # backend/debug_dumps/<document_id>/. Set KNOWLEDGE_DEBUG_DUMP=1 in backend/.env.
    # Remove this field once the model_qa ingestion issue is diagnosed.
    KNOWLEDGE_DEBUG_DUMP: bool = False

    # Database
    # Either set DATABASE_URL directly (asyncpg driver), or provide the discrete
    # PG* components below (e.g. Azure Postgres connection info) and DATABASE_URL
    # is assembled from them at startup (see _assemble_database_url).
    DATABASE_URL: str = _DEFAULT_DATABASE_URL

    # Discrete Postgres connection parts (Azure flexible server style). When PGHOST
    # is set and DATABASE_URL is left at its default, these are used to build the
    # asyncpg URL with SSL required (Azure mandates TLS).
    PGHOST: str = ""
    PGUSER: str = ""
    PGPORT: int = 5432
    PGDATABASE: str = ""
    PGPASSWORD: str = ""

    # DB connection pool (per process: FastAPI + each Celery worker keep their own).
    # Sized so (API + worker×concurrency + beat) stays comfortably under Azure PG
    # max_connections. Raise on a larger PG tier / higher worker concurrency.
    DB_POOL_SIZE: int = 8
    DB_MAX_OVERFLOW: int = 12

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
    # API versions for the tiered chat deployments (gpt-5 "thinking", gpt-5-mini "fast").
    AZURE_OPENAI_API_VERSION_THINKING: str = "2025-01-01-preview"
    AZURE_OPENAI_API_VERSION_FAST: str = "2025-01-01-preview"
    # Model tiers (see ai/model_router.py):
    #   MODEL_REASONING (gpt-5.5)  → high-intelligence reasoning (generation, evaluation, tutors)
    #   MODEL_CHAT_THINKING (gpt-5) → typed text/vision extraction (printed/scanned docs, question papers)
    #   MODEL_CHAT_FAST (gpt-5-mini) → lower-intelligence work (semantic chunking)
    # Gemini (MODEL_VISION) is handwriting-only.
    MODEL_REASONING: str = "gpt-5.5"
    MODEL_CHAT_THINKING: str = "gpt-5"
    MODEL_CHAT_FAST: str = "gpt-5-mini"
    MODEL_EMBEDDING: str = "text-embedding-3-large"
    MODEL_TRANSCRIPTION: str = "gpt-4o-transcribe"
    EMBEDDING_DIMENSIONS: int = 3072

    # Semantic-chunking tier (knowledge ingest). Chunking is a ONE-TIME cost per
    # document whose output quality (segmentation + verbatim Devanagari fidelity)
    # underpins all downstream retrieval, so gpt-5 "thinking" is the default. Set to
    # "fast" to use the cheaper gpt-5-mini. Only affects get_provider("chunking");
    # all other tiers (routing, etc.) are unchanged. Read in ai/model_router.py.
    CHUNKING_MODEL_TIER: str = "thinking"   # "thinking" (gpt-5) | "fast" (gpt-5-mini)

    # AI Models — Google Gemini (VISION ONLY: handwriting extraction, structure pass,
    # annotation locator). Reasoning/embeddings/transcription stay on Azure OpenAI.
    GEMINI_API_KEY: str = ""
    MODEL_VISION: str = ""
    # Ordered fallback CHAIN (comma-separated) tried in order the moment MODEL_VISION is
    # unavailable (503 "high demand" overload / 429 / 404). Each model except the last is
    # given a single fast attempt with NO same-model retry waits, so a down model is never
    # re-hit — the call just walks to the next tier (a different, higher-capacity pool).
    # Quality-ordered, e.g. gemini-3.1-pro-preview,gemini-3-flash-preview,gemini-3.1-flash-lite.
    # A single value still works (old format). Empty → no fallback (raise).
    MODEL_VISION_FALLBACK: str = ""

    # AI / worker timeouts (seconds)
    AI_REQUEST_TIMEOUT_SECONDS: int = 180   # per Azure OpenAI call
    GEMINI_REQUEST_TIMEOUT_SECONDS: int = 180  # per Gemini vision call
    AI_MAX_RETRIES: int = 3                  # transient-error retries per AI call
    # Gemini free tier is rate-limited PER MINUTE, so on a rate-limit error we wait a
    # full minute before retrying (a short exponential backoff would just hit it again).
    GEMINI_RATE_LIMIT_RETRY_SECONDS: int = 60
    GEMINI_RATE_LIMIT_MAX_RETRIES: int = 3
    TASK_TIMEOUT_SECONDS: int = 7200         # hard ceiling for a single Celery job (2h; large 1000-page books can take a while)

    # URLs
    FRONTEND_URL: str = "http://localhost:5173"
    BACKEND_URL: str = "http://localhost:8000"

    # Optional shared storage URI for the HTTP rate limiter (e.g. the Redis URL) so the
    # limit is enforced across multiple API processes. Empty → in-process memory storage.
    RATELIMIT_STORAGE_URI: str = ""

    # Seed defaults
    DEFAULT_ADMIN_EMAIL: str = "admin@neurafix.ai"
    DEFAULT_ADMIN_PASSWORD: str = "Admin@123"
    DEFAULT_ADMIN_NAME: str = "Institute Admin"

    @model_validator(mode="after")
    def _assemble_database_url(self) -> "Settings":
        """Build an asyncpg DATABASE_URL from discrete PG* parts when an explicit
        DATABASE_URL was not supplied. The password is percent-encoded so special
        characters (@, #, etc.) don't corrupt the URL, and SSL is required because
        Azure Postgres rejects non-TLS connections."""
        explicit = bool(self.DATABASE_URL) and self.DATABASE_URL != _DEFAULT_DATABASE_URL
        if not explicit and self.PGHOST:
            user = quote_plus(self.PGUSER.strip())
            password = quote_plus(self.PGPASSWORD.strip())
            host = self.PGHOST.strip()
            database = self.PGDATABASE.strip()
            self.DATABASE_URL = (
                f"postgresql+asyncpg://{user}:{password}@{host}:{self.PGPORT}/{database}?ssl=require"
            )
        return self

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
