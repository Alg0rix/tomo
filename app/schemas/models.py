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
    role: str = ""
    workplace_id: str = ""
    workplace_scope: str = "single"  # single | list | all_tunnels | all
    workplace_ids: list[str] = Field(default_factory=list)
    enabled: bool = True
    is_super: bool = False
    tool_count: int = 0
    channel_count: int = 0
    skill_count: int = 0
    busy: bool = False
    created_at: str = ""


class AgentCreate(BaseModel):
    """Create an agent. ``id`` is optional — server auto-slugs from ``name``."""

    id: str | None = Field(
        default=None, min_length=2, max_length=64, pattern=r"^[a-z0-9_]+$"
    )
    name: str = Field(min_length=1, max_length=80)
    description: str = ""
    model_id: str | None = None
    role: str = ""
    workplace_id: str = ""
    workplace_ids: list[str] = Field(default_factory=list)
    workplace_scope: str = "single"  # single | list | all_tunnels | all
    system_prompt: str | None = Field(default=None, max_length=12_000)


class AgentGenerateIn(BaseModel):
    """Brief for LLM-assisted agent draft (not persisted until create)."""

    brief: str = Field(min_length=3, max_length=2000)


class AgentDraft(BaseModel):
    """Generated agent preview before save."""

    name: str
    role: str = ""
    description: str = ""
    suggested_id: str = ""
    system_prompt: str = ""


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    description: str | None = None
    model_id: str | None = None
    role: str | None = None
    workplace_id: str | None = None
    workplace_ids: list[str] | None = None
    workplace_scope: str | None = None  # single | list | all_tunnels | all
    enabled: bool | None = None


class ChatMessageIn(BaseModel):
    message: str
    user_id: str = "web"
    session_id: str | None = None


class SessionCreate(BaseModel):
    """Create/update session membership.

    Empty ``agent_ids`` means full swarm (all enabled agents).
    ``workplace_id`` is the chat default workplace (prefer local).
    """

    agent_ids: list[str] = Field(default_factory=list)
    user_id: str = "web"
    coordinator_id: str | None = None
    workplace_id: str | None = None


class SessionWorkplaceIn(BaseModel):
    """Set or clear a chat's default workplace."""

    workplace_id: str = ""


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


class LLMProfileCreate(BaseModel):
    """Create an LLM profile. ``id`` optional — auto from ``name``."""

    id: str | None = Field(
        default=None, min_length=2, max_length=64, pattern=r"^[a-z0-9_]+$"
    )
    name: str = Field(min_length=1, max_length=80)
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    enabled: bool = True


class LLMProfileUpdate(BaseModel):
    """Update an LLM profile. A blank ``api_key`` keeps the existing ciphertext."""

    name: str | None = Field(default=None, max_length=80)
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    enabled: bool | None = None


class WorkplaceCreate(BaseModel):
    """Create a workplace (local / ssh / tunnel). ``id`` optional — auto from ``name``."""

    id: str | None = Field(
        default=None, min_length=2, max_length=64, pattern=r"^[a-z0-9_]+$"
    )
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["local", "ssh", "tunnel"] = "local"
    host: str = ""
    root_path: str = ""
    ssh_host: str = ""
    ssh_port: int = 22
    ssh_user: str = ""
    ssh_password: str = ""
    ssh_key: str = ""


class WorkplaceUpdate(BaseModel):
    """Update a workplace. Blank password/key keeps existing ciphertext."""

    name: str | None = Field(default=None, max_length=80)
    kind: Literal["local", "ssh", "tunnel"] | None = None
    host: str | None = None
    root_path: str | None = None
    ssh_host: str | None = None
    ssh_port: int | None = None
    ssh_user: str | None = None
    ssh_password: str | None = None
    ssh_key: str | None = None


class KnowledgeEntryCreate(BaseModel):
    """Create a knowledge base entry (Slice E)."""

    id: str | None = Field(
        default=None, min_length=2, max_length=64, pattern=r"^[a-z0-9_]+$"
    )
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=20_000)
    tags: list[str] = Field(default_factory=list)


class KnowledgeEntryUpdate(BaseModel):
    """Update a knowledge base entry."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, max_length=20_000)
    tags: list[str] | None = None


class ScheduleCreate(BaseModel):
    """Create an interval/cron schedule (Alpha Slice G)."""

    id: str | None = Field(
        default=None, min_length=2, max_length=64, pattern=r"^[a-z0-9_]+$"
    )
    name: str = Field(min_length=1, max_length=120)
    agent_id: str = Field(min_length=1, max_length=64)
    cron: str = ""
    interval_seconds: int | None = Field(default=None, ge=1, le=86400 * 30)
    message: str = Field(default="", max_length=4000)
    enabled: bool = True


class ScheduleUpdate(BaseModel):
    """Update a schedule (enable/disable, interval, message, …)."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    agent_id: str | None = Field(default=None, min_length=1, max_length=64)
    cron: str | None = None
    interval_seconds: int | None = Field(default=None, ge=1, le=86400 * 30)
    message: str | None = Field(default=None, max_length=4000)
    enabled: bool | None = None
