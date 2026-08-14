"""MCP server/item API request schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class McpServerCreate(BaseModel):
    id: str | None = Field(default=None, min_length=2, max_length=64, pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=80)
    transport: Literal["stdio", "streamable_http"]
    command: str = Field(default="", max_length=400)
    args: list[str] = Field(default_factory=list, max_length=64)
    url: str = Field(default="", max_length=2000)
    env: dict[str, str] | None = None
    headers: dict[str, str] | None = None
    enabled: bool = True


class McpServerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    transport: Literal["stdio", "streamable_http"] | None = None
    command: str | None = Field(default=None, max_length=400)
    args: list[str] | None = Field(default=None, max_length=64)
    url: str | None = Field(default=None, max_length=2000)
    env: dict[str, str] | None = None
    headers: dict[str, str] | None = None
    enabled: bool | None = None


class McpItemEnabled(BaseModel):
    enabled: bool


class McpResourceRead(BaseModel):
    uri: str = Field(min_length=1, max_length=4000)


class McpPromptGet(BaseModel):
    name: str = Field(min_length=1, max_length=400)
    arguments: dict[str, str] = Field(default_factory=dict)


__all__ = [
    "McpServerCreate",
    "McpServerUpdate",
    "McpItemEnabled",
    "McpResourceRead",
    "McpPromptGet",
]
