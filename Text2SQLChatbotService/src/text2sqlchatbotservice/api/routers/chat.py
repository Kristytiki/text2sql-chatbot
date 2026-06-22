"""Chat session endpoints — backed by per-session Strands Agents."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from text2sqlchatbotservice.agent import build_chat_agent
from text2sqlchatbotservice.api.auth import require_api_key
from text2sqlchatbotservice.guardrails import dispatch as canned_dispatch
from text2sqlchatbotservice.api.schemas import (
    CreateSessionRequest,
    CreateSessionResponse,
    HistoryResponse,
    HistoryTurn,
    SendMessageRequest,
    SendMessageResponse,
    SqlEvidence,
)
from text2sqlchatbotservice.memory import ChatSession

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(require_api_key)])


GREETING = (
    "👋 Hi! I'm a **Census Data Analyst**.\n\n"
    "I can answer questions about the US population using the Snowflake "
    "US Open Census dataset — *SafeGraph ACS 5-year estimates*, "
    "**2019** and **2020** snapshots, covering **~242k** census block "
    "groups and **~7,700** metrics across 29 ACS topics: population, "
    "race, ethnicity, age, income, poverty, housing, employment, "
    "education, commute, language, and more.\n\n"
    "**Try one of:**\n\n"
    "- *What's the total population of California in 2020?*\n"
    "- *How many Hispanic or Latino people live in Texas?*\n"
    "- *Compare population in 2019 vs 2020 for the top 5 states*\n"
    "- *How many vacant housing units are there in New York?*"
)


@router.post("/sessions", response_model=CreateSessionResponse, status_code=201)
def create_session(_: CreateSessionRequest, request: Request) -> CreateSessionResponse:
    state = request.app.state
    sid = state.sessions.new_session_id()
    agent = build_chat_agent(
        settings=state.settings,
        session_id=sid,
        tools=state.tools,
    )
    state.sessions.add(ChatSession(session_id=sid, agent=agent))
    return CreateSessionResponse(session_id=sid, greeting=GREETING)


@router.post("/sessions/{session_id}/messages", response_model=SendMessageResponse)
def send_message(session_id: str, body: SendMessageRequest, request: Request) -> SendMessageResponse:
    state = request.app.state
    session = state.sessions.get(session_id)
    if session is None:
        raise HTTPException(404, f"session {session_id!r} not found")

    # Input guardrail.
    in_verdict = state.guardrail.apply(body.message, source="INPUT")
    if in_verdict.blocked:
        return SendMessageResponse(
            reply=in_verdict.output, blocked=True, block_reason=in_verdict.reason,
        )

    # Snapshot the message-history length BEFORE running the agent so we can
    # extract evidence from this turn only (otherwise stale SQL from prior
    # turns leaks into every reply — see code-review finding #1).
    with session.lock:
        baseline = len(session.agent.messages or [])
        try:
            result = session.agent(body.message)
        except Exception as exc:
            logger.exception("agent call failed for session %s", session_id)
            # Truncate the conversation back to the pre-call baseline so a
            # half-formed tool_use without a matching tool_result doesn't
            # poison every subsequent turn (code-review finding #8).
            try:
                session.agent.messages[baseline:] = []
            except Exception:
                logger.exception("failed to roll back session %s", session_id)
            # Generic message to the client; details stay in server logs only.
            raise HTTPException(502, "Upstream model error. Please try again.") from exc
        new_messages = list((session.agent.messages or [])[baseline:])

    reply_text = _extract_text(result)

    # Capability-boundary canned dispatch: if the LLM emitted a [REFUSE:cat]
    # tag, replace its body with a deterministic canned response and mark the
    # turn as blocked. We skip the output guardrail in that case — the canned
    # text is hand-written and already safe.
    canned = canned_dispatch(reply_text)
    if canned.matched:
        logger.info("canned refusal %s for session %s", canned.category, session_id)
        return SendMessageResponse(
            reply=canned.text, blocked=True, block_reason=f"refused:{canned.category}",
        )

    # Output guardrail.
    out_verdict = state.guardrail.apply(reply_text, source="OUTPUT")
    if out_verdict.blocked:
        return SendMessageResponse(
            reply=out_verdict.output, blocked=True, block_reason=out_verdict.reason,
        )

    evidence = _extract_evidence(new_messages)
    return SendMessageResponse(reply=reply_text, evidence=evidence)


@router.get("/sessions/{session_id}/history", response_model=HistoryResponse)
def get_history(session_id: str, request: Request) -> HistoryResponse:
    session = request.app.state.sessions.get(session_id)
    if session is None:
        raise HTTPException(404, f"session {session_id!r} not found")

    turns: list[HistoryTurn] = []
    for m in session.agent.messages or []:
        role = m.get("role", "")
        if role not in ("user", "assistant"):
            continue
        for block in m.get("content", []) or []:
            text = block.get("text") if isinstance(block, dict) else None
            if text:
                turns.append(HistoryTurn(role=role, text=text))
    return HistoryResponse(session_id=session_id, turns=turns)


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, request: Request) -> None:
    if not request.app.state.sessions.delete(session_id):
        raise HTTPException(404, f"session {session_id!r} not found")


def _extract_text(agent_result: object) -> str:
    """Strands Agent.__call__ returns a result object — pull out its .message text."""
    msg = getattr(agent_result, "message", None)
    if isinstance(msg, dict):
        for block in msg.get("content", []) or []:
            if isinstance(block, dict) and "text" in block:
                return block["text"]
    return str(agent_result)


# Strands wraps each tool call into a toolUse block (with toolUseId+name)
# followed in a later message by a matching toolResult block. We pair them
# by toolUseId so we only surface evidence whose source tool was the one
# that actually issues SQL — robust to future tools whose JSON happens to
# have a `sql` field for unrelated reasons (code-review finding "altitude").
SQL_TOOL_NAMES = {"semantic_query"}


def _extract_evidence(messages: list[dict]) -> list[SqlEvidence]:
    """Surface SqlEvidence from THIS turn's tool results only.

    `messages` should be the per-turn slice (post-baseline), not the full
    history — otherwise prior-turn evidence leaks into every reply.
    """
    sql_tool_use_ids: set[str] = set()
    for m in messages:
        for block in m.get("content", []) or []:
            if not isinstance(block, dict):
                continue
            tu = block.get("toolUse")
            if isinstance(tu, dict) and tu.get("name") in SQL_TOOL_NAMES:
                tu_id = tu.get("toolUseId")
                if isinstance(tu_id, str):
                    sql_tool_use_ids.add(tu_id)

    evidence: list[SqlEvidence] = []
    for m in messages:
        for block in m.get("content", []) or []:
            if not isinstance(block, dict):
                continue
            tr = block.get("toolResult")
            if not isinstance(tr, dict):
                continue
            if tr.get("toolUseId") not in sql_tool_use_ids:
                continue
            for c in tr.get("content", []) or []:
                text = c.get("text") if isinstance(c, dict) else None
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict) or "sql" not in payload:
                    continue
                # Tool JSON now uses `rows_total_in_result` (full result size)
                # vs `rows_returned` (rows shipped to the LLM). Fall back to
                # the legacy name for safety.
                total = payload.get("rows_total_in_result", payload.get("row_count", 0))
                evidence.append(
                    SqlEvidence(
                        sql=payload["sql"],
                        columns=payload.get("columns", []),
                        rows=payload.get("rows", []),
                        row_count=total,
                        truncated=payload.get("truncated", False),
                    )
                )
    return evidence
