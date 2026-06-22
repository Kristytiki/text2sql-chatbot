"""HTTP-layer tests via FastAPI TestClient.

We bypass the real lifespan (which would connect to Snowflake/Bedrock) and
inject a stub app.state, so these run fully offline. They pin the auth flow
and health contract — not agent behaviour.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from text2sqlchatbotservice.api.routers import chat as chat_router


class _StubGuardrail:
    enabled = False

    def apply(self, text, *, source):
        from text2sqlchatbotservice.guardrails.bedrock_guardrail import GuardrailVerdict

        return GuardrailVerdict(blocked=False, action="NONE", output=text)


def _make_app(api_key):
    """Bare app with the chat router + health, stub state, no lifespan."""
    app = FastAPI()
    app.include_router(chat_router.router)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    settings = type("S", (), {"api_key": api_key})()
    app.state.settings = settings
    app.state.guardrail = _StubGuardrail()
    return app


def test_health_ok():
    client = TestClient(_make_app(api_key="secret"))
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_chat_without_key_is_401():
    client = TestClient(_make_app(api_key="secret"))
    r = client.post("/chat/sessions", json={"user_name": "x"})
    assert r.status_code == 401


def test_chat_with_wrong_key_is_401():
    client = TestClient(_make_app(api_key="secret"))
    r = client.post(
        "/chat/sessions", json={"user_name": "x"}, headers={"X-API-Key": "nope"}
    )
    assert r.status_code == 401


def test_send_message_unknown_session_404_with_key():
    # Valid key passes auth; unknown session → 404 (not 401). Needs a sessions
    # registry on state; a dict-like stub returning None for .get suffices.
    app = _make_app(api_key="secret")
    app.state.sessions = type("Reg", (), {"get": staticmethod(lambda sid: None)})()
    client = TestClient(app)
    r = client.post(
        "/chat/sessions/does-not-exist/messages",
        json={"message": "hi"},
        headers={"X-API-Key": "secret"},
    )
    assert r.status_code == 404
