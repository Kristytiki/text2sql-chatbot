"""Wire-format Pydantic models for the HTTP API."""
from __future__ import annotations

from pydantic import BaseModel, Field


class CreateSessionRequest(BaseModel):
    user_name: str = Field(default="Analyst", min_length=1, max_length=64)


class CreateSessionResponse(BaseModel):
    session_id: str
    greeting: str


class SendMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class SqlEvidence(BaseModel):
    sql: str
    columns: list[str]
    rows: list[list]
    row_count: int
    truncated: bool = False


class SendMessageResponse(BaseModel):
    reply: str
    blocked: bool = False
    block_reason: str | None = None
    evidence: list[SqlEvidence] = Field(default_factory=list)


class HistoryTurn(BaseModel):
    role: str   # "user" | "assistant"
    text: str


class HistoryResponse(BaseModel):
    session_id: str
    turns: list[HistoryTurn]
