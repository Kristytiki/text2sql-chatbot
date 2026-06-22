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

    aws_region: str = "us-east-1"
    bedrock_model_id: str = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    bedrock_guardrail_id: str = ""
    bedrock_guardrail_version: str = "DRAFT"

    cors_origins: tuple[str, ...] = ("http://localhost:5173",)
    # Shared secret guarding the /chat API. Empty string = auth disabled
    # (local dev only). Set API_KEY in .env before exposing publicly.
    api_key: str = ""
    session_dir: str = "./.sessions"
    log_level: str = "INFO"
    max_row_limit: int = 10000

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
