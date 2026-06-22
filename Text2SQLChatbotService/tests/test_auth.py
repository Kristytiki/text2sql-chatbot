"""Shared-secret API-key dependency — the auth boundary on /chat."""
import pytest
from fastapi import HTTPException

from text2sqlchatbotservice.api.auth import require_api_key


class _Settings:
    def __init__(self, api_key):
        self.api_key = api_key


class _Request:
    """Minimal stand-in for a Starlette Request: .app.state.settings + .headers."""

    def __init__(self, configured_key, supplied_key=None):
        settings = _Settings(configured_key)
        state = type("State", (), {"settings": settings})()
        self.app = type("App", (), {"state": state})()
        self.headers = {"X-API-Key": supplied_key} if supplied_key is not None else {}


def test_correct_key_passes():
    require_api_key(_Request("secret", "secret"))  # no raise


def test_wrong_key_rejected():
    with pytest.raises(HTTPException) as ei:
        require_api_key(_Request("secret", "nope"))
    assert ei.value.status_code == 401


def test_missing_key_rejected():
    with pytest.raises(HTTPException) as ei:
        require_api_key(_Request("secret", None))
    assert ei.value.status_code == 401


def test_empty_config_is_noop():
    # No key configured → dev mode, dependency must not block.
    require_api_key(_Request("", None))  # no raise
