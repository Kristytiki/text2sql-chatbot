"""Build a per-session Strands Agent (Bedrock Claude Sonnet)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.models import BedrockModel
from strands.session.file_session_manager import FileSessionManager

from text2sqlchatbotservice.agent.system_prompt import build_system_prompt
from text2sqlchatbotservice.config import Settings

logger = logging.getLogger(__name__)


def build_chat_agent(
    *,
    settings: Settings,
    session_id: str,
    tools: list[Callable[..., Any]],
    window_size: int = 20,
) -> Agent:
    Path(settings.session_dir).mkdir(parents=True, exist_ok=True)

    model = BedrockModel(
        model_id=settings.bedrock_model_id,
        region_name=settings.aws_region,
    )

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
