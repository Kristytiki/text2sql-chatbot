"""Build a per-session Strands Agent (Bedrock or Anthropic Claude — see settings.llm_provider)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.session.file_session_manager import FileSessionManager

from text2sqlchatbotservice.agent.system_prompt import build_system_prompt
from text2sqlchatbotservice.config import Settings

logger = logging.getLogger(__name__)


def _build_model(settings: Settings):
    """Select the LLM provider from settings.llm_provider.

    "bedrock"  — AWS Bedrock (default; uses host AWS creds).
    "anthropic" — direct Anthropic API (only needs anthropic_api_key, no AWS).
    """
    provider = settings.llm_provider.lower()
    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise RuntimeError(
                "llm_provider=anthropic but ANTHROPIC_API_KEY is not set"
            )
        # Imported lazily so the optional `anthropic` dep is only required when
        # this provider is actually selected (pip install -e ".[anthropic]").
        from strands.models.anthropic import AnthropicModel

        return AnthropicModel(
            client_args={"api_key": settings.anthropic_api_key},
            model_id=settings.anthropic_model_id,
            max_tokens=settings.anthropic_max_tokens,
        )
    if provider == "bedrock":
        from strands.models import BedrockModel

        return BedrockModel(
            model_id=settings.bedrock_model_id,
            region_name=settings.aws_region,
        )
    raise ValueError(f"unknown llm_provider: {settings.llm_provider!r}")


def build_chat_agent(
    *,
    settings: Settings,
    session_id: str,
    tools: list[Callable[..., Any]],
    window_size: int = 20,
) -> Agent:
    Path(settings.session_dir).mkdir(parents=True, exist_ok=True)

    model = _build_model(settings)

    conv = SlidingWindowConversationManager(window_size=window_size)
    sess = FileSessionManager(session_id=session_id, storage_dir=settings.session_dir)

    return Agent(
        model=model,
        system_prompt=build_system_prompt(),
        tools=tools,
        conversation_manager=conv,
        session_manager=sess,
        callback_handler=None,
    )
