#!/usr/bin/env python3
"""Tiny SDK-backed MCP test fixture: one tool, one resource, one prompt.

Used only by tests (both stdio and Streamable HTTP transports) — never a
real network service, and it talks no protocol on stdout besides MCP's own
framed JSON-RPC (writing anything else there would corrupt the stdio
transport for real clients, so this file must stay quiet).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("tomo-test-fixture")


@mcp.tool()
def echo(text: str) -> str:
    """Echo back the given text, prefixed so the caller can tell it's real."""
    return f"echo: {text}"


@mcp.resource("test://greeting")
def greeting() -> str:
    """A static text resource."""
    return "hello from fixture"


@mcp.prompt()
def review(topic: str) -> str:
    """A prompt with one required string argument."""
    return f"Please review: {topic}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
