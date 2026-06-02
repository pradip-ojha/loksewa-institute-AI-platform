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

    # URLs
    FRONTEND_URL: str = "http://localhost:5173"
    BACKEND_URL: str = "http://localhost:8000"

    # Seed defaults
    DEFAULT_ADMIN_EMAIL: str = "admin@neurafix.ai"
    DEFAULT_ADMIN_PASSWORD: str = "Admin@123"
    DEFAULT_ADMIN_NAME: str = "Institute Admin"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
