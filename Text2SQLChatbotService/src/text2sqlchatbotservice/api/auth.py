"""Shared-secret API-key auth for the /chat router.

A single secret is read from settings (``API_KEY`` env var). Clients send it in
the ``X-API-Key`` header. When the secret is unset the dependency is a no-op so
local dev keeps working without a key — but the app logs a loud warning at
startup (see app.py) so an unauthenticated public deployment is never silent.
"""
from __future__ import annotations

import hmac

from fastapi import HTTPException, Request


def require_api_key(request: Request) -> None:
    """FastAPI dependency: 401 unless the request carries the configured key.

    No-op when no key is configured (local dev).
    """
    expected: str = request.app.state.settings.api_key
    if not expected:
        return  # auth disabled

    supplied = request.headers.get("X-API-Key", "")
    # Constant-time compare to avoid timing oracles on the secret.
    if not (supplied and hmac.compare_digest(supplied, expected)):
        raise HTTPException(status_code=401, detail="invalid or missing API key")


__all__ = ["require_api_key"]
