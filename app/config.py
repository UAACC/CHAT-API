"""
Application configuration from environment variables.
"""

from pydantic_settings import BaseSettings
from typing import Literal, Optional
from functools import lru_cache


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    # App Identity (customize for your project)
    app_name: str = "CHAT-API"
    app_description: str = "Reusable AI-powered chat assistant backend"

    # LLM Provider Configuration
    llm_provider: Literal["openai", "anthropic", "gemini"] = "openai"
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""

    # Model Configuration
    openai_model: str = "gpt-4o-mini"
    anthropic_model: str = "claude-3-haiku-20240307"
    # Flash-Lite has the highest free-tier daily request quota and does not
    # "think" by default. Non-lite Flash models spend most of MAX_TOKENS on
    # thinking and return truncated answers unless MAX_TOKENS is raised a lot.
    gemini_model: str = "gemini-3.1-flash-lite"

    # Custom System Prompt (optional - overrides default)
    # Can be a string or path to a file
    system_prompt_en: Optional[str] = None
    system_prompt_zh: Optional[str] = None
    system_prompt_file: Optional[str] = None  # Path to prompts JSON file

    # Generation Settings
    max_tokens: int = 512  # Reduced from 1024 to save tokens
    temperature: float = 0.7

    # Rate Limiting
    rate_limit_requests: int = 20
    rate_limit_window: int = 60  # seconds

    # Cost Protection
    max_input_length: int = 500  # Max characters per user message
    max_messages_per_session: int = 20  # Max messages in conversation
    max_context_messages: int = 10  # Only send last N messages to LLM

    # CORS Configuration (customize for your frontend domains)
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # Server Configuration
    app_env: str = "production"
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8080

    # RAG - Pinecone Configuration
    pinecone_api_key: str = ""
    pinecone_index: str = "chat-api-rag-ahstudio"

    # RAG - Google Cloud Storage Configuration
    gcs_bucket: str = "chat-api-rag-documents"
    gcs_prefix: str = "documents"

    # RAG - Embedding Configuration
    embedding_model: str = "text-embedding-3-small"

    # RAG - Document Processing
    rag_chunk_size: int = 512
    rag_chunk_overlap: int = 64
    rag_max_file_size_mb: int = 10

    # RAG - Query Configuration
    rag_default_top_k: int = 5
    rag_min_score_threshold: float = 0.1

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse CORS origins into a list (supports comma or semicolon separator)."""
        # Support both comma and semicolon as separators (semicolon for Cloud Run)
        origins = self.cors_origins.replace(";", ",")
        return [origin.strip() for origin in origins.split(",") if origin.strip()]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
