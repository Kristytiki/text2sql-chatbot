"""LLM provider selection (settings.llm_provider) in the agent factory."""
import pytest

from text2sqlchatbotservice.agent.factory import _build_model


class _Settings:
    llm_provider = "bedrock"
    bedrock_model_id = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    aws_region = "us-east-1"
    anthropic_api_key = ""
    anthropic_model_id = "claude-sonnet-4-5-20250929"
    anthropic_max_tokens = 4096


def test_bedrock_builds_offline():
    s = _Settings()
    s.llm_provider = "bedrock"
    model = _build_model(s)
    assert type(model).__name__ == "BedrockModel"


def test_anthropic_without_key_raises():
    s = _Settings()
    s.llm_provider = "anthropic"
    s.anthropic_api_key = ""
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        _build_model(s)


def test_unknown_provider_raises():
    s = _Settings()
    s.llm_provider = "gpt5"
    with pytest.raises(ValueError, match="unknown llm_provider"):
        _build_model(s)
