"""Session registry — in-process map plus on-demand restore from disk.

Strands' `FileSessionManager` writes message history to `<session_dir>/<sid>/`
on every turn, which means a session can outlive an uvicorn restart. Without a
restore path the persistence is dead weight: the next message lookup misses
the in-memory map and 404s. This registry plugs that gap by:

  * Maintaining the live in-memory map (fast path for active sessions).
  * On `get()` miss, checking `<session_dir>/<sid>` and lazily rebuilding the
    Agent if a session directory exists. Strands' Agent constructor with
    a matching session_id will hydrate `agent.messages` from disk.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from strands import Agent

logger = logging.getLogger(__name__)


@dataclass
class ChatSession:
    session_id: str
    agent: "Agent"
    lock: Lock = field(default_factory=Lock, repr=False, compare=False)


# Restore callback: given a session_id, return a fresh Agent that loads its
# history from disk. Wired by `app.py` lifespan so the registry doesn't need
# to depend on factory + tools directly.
RestoreFn = Callable[[str], "Agent"]


class SessionRegistry:
    def __init__(
        self,
        *,
        session_dir: str | Path | None = None,
        restore: RestoreFn | None = None,
    ) -> None:
        self._sessions: dict[str, ChatSession] = {}
        self._lock = Lock()
        self._session_dir = Path(session_dir) if session_dir else None
        self._restore = restore

    def new_session_id(self) -> str:
        return uuid.uuid4().hex

    def add(self, session: ChatSession) -> None:
        with self._lock:
            self._sessions[session.session_id] = session
        logger.info("registered session %s", session.session_id)

    def get(self, session_id: str) -> ChatSession | None:
        # Fast path: live session.
        with self._lock:
            existing = self._sessions.get(session_id)
        if existing is not None:
            return existing

        # Cold path: rebuild from disk if available.
        if self._session_dir is None or self._restore is None:
            return None
        sess_path = self._session_dir / f"session_{session_id}"
        if not sess_path.exists():
            sess_path = self._session_dir / session_id  # alt naming
            if not sess_path.exists():
                return None

        with self._lock:
            # Re-check after acquiring the lock — another thread may have won.
            existing = self._sessions.get(session_id)
            if existing is not None:
                return existing
            try:
                agent = self._restore(session_id)
            except Exception:
                logger.exception("failed to restore session %s from disk", session_id)
                return None
            session = ChatSession(session_id=session_id, agent=agent)
            self._sessions[session_id] = session
            logger.info("restored session %s from disk", session_id)
            return session

    def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None
