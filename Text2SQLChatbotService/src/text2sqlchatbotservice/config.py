"""Centralised env-loaded settings."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    snowflake_account: str
    snowflake_user: str
    snowflake_password: str
    snowflake_warehouse: str = "COMPUTE_WH"
    snowflake_database: str = "US_OPEN_CENSUS_DATA__NEIGHBORHOOD_INSIGHTS__FREE_DATASET"
    snowflake_schema: str = "PUBLIC"
    snowflake_role: str = "PUBLIC"

    # LLM provider switch. "bedrock" (default, uses AWS creds on the host) or
    # "anthropic" (direct Anthropic API — only needs ANTHROPIC_API_KEY, no AWS).
    # Flip to "anthropic" when running off the Amazon network.
    llm_provider: str = "bedrock"

    aws_region: str = "us-east-1"
    bedrock_model_id: str = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    bedrock_guardrail_id: str = ""
    bedrock_guardrail_version: str = "DRAFT"

    # Used only when llm_provider == "anthropic".
    anthropic_api_key: str = ""
    anthropic_model_id: str = "claude-sonnet-4-5-20250929"
    anthropic_max_tokens: int = 4096

    cors_origins: tuple[str, ...] = ("http://localhost:5173",)
    # Directory of the built UI (Vite `dist/`). When set and present, the
    # backend serves it at "/" so the whole app is one origin (no CORS). Empty
    # = API-only (local dev with the Vite dev server on :5173).
    ui_dist_dir: str = ""
    # Bind host/port. Render injects $PORT; bind 0.0.0.0 in containers.
    host: str = "127.0.0.1"
    port: int = 8000
    # Shared secret guarding the /chat API. Empty string = auth disabled
    # (local dev only). Set API_KEY in .env before exposing publicly.
    api_key: str = ""
    session_dir: str = "./.sessions"
    log_level: str = "INFO"
    max_row_limit: int = 10000

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
