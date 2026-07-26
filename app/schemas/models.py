"""Pydantic schemas for API request/response bodies.

These mirror the stub store's data shapes so the real agent backend can swap
in by implementing the same contracts.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Agent(BaseModel):
    id: str
    name: str
    description: str = ""
    model_id: str | None = None
    enabled: bool = True
    is_super: bool = False
    tool_count: int = 0
    channel_count: int = 0
    skill_count: int = 0
    busy: bool = False
    created_at: str = ""


class AgentCreate(BaseModel):
    id: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=80)
    description: str = ""
    model_id: str | None = None


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    description: str | None = None
    model_id: str | None = None
    enabled: bool | None = None


class ChatMessageIn(BaseModel):
    message: str
    user_id: str = "web"
    session_id: str | None = None


class SessionCreate(BaseModel):
    agent_ids: list[str] = Field(min_length=1)
    user_id: str = "web"
    coordinator_id: str | None = None


class HomeSessionIn(BaseModel):
    """Dashboard chat-home start — no agent picker; coordinator only."""

    message: str = ""
    user_id: str = "web"


class SessionChatIn(BaseModel):
    message: str
    user_id: str = "web"


class ChatEntry(BaseModel):
    """One JSONL-style history entry (replay format)."""

    type: Literal["user", "final", "thinking", "tool_call", "tool_output", "intermediate", "error", "delegate"]
    content: str = ""
    agent_id: str | None = None
    function: str | None = None
    params: dict | None = None
    error: bool = False
    ts: float = 0.0


class Stats(BaseModel):
    agent_count: int = 0
    enabled_agent_count: int = 0
    session_count: int = 0
    tool_count: int = 0
    channel_count: int = 0
    active_channel_count: int = 0
    skill_count: int = 0
