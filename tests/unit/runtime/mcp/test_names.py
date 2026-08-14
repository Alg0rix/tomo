"""Stable MCP runtime-tool-id normalization/round-tripping."""

from __future__ import annotations

import pytest

from app.runtime.mcp.discovery import paginate_mcp_list
from app.runtime.mcp.names import is_mcp_runtime_id, runtime_tool_id, split_runtime_tool_id


def test_runtime_tool_id_round_trips() -> None:
    runtime_id = runtime_tool_id("github", "create_issue")
    assert runtime_id == "mcp__github__create_issue"
    assert split_runtime_tool_id(runtime_id) == ("github", "create_issue")


def test_runtime_tool_id_sanitizes_unsafe_characters() -> None:
    runtime_id = runtime_tool_id("my server!", "do thing/now")
    assert runtime_id == "mcp__my_server__do_thing_now"
    assert split_runtime_tool_id(runtime_id) == ("my_server", "do_thing_now")


def test_runtime_tool_id_deterministic_shortening_for_long_names() -> None:
    long_server = "a" * 40
    long_tool = "b" * 40
    runtime_id = runtime_tool_id(long_server, long_tool)
    assert len(runtime_id) <= 64
    # Deterministic: same inputs -> same shortened id.
    assert runtime_tool_id(long_server, long_tool) == runtime_id


def test_split_runtime_tool_id_rejects_non_mcp_names() -> None:
    assert split_runtime_tool_id("bash") is None
    assert split_runtime_tool_id("mcp__onlyserver") is None


def test_is_mcp_runtime_id() -> None:
    assert is_mcp_runtime_id("mcp__github__create_issue")
    assert not is_mcp_runtime_id("bash")


@pytest.mark.asyncio
async def test_pagination_follows_opaque_cursors() -> None:
    cursors = []
    pages = {None: ([1], "next-a"), "next-a": ([2], "next-b"), "next-b": ([3], None)}

    async def fetch(cursor):
        cursors.append(cursor)
        values, next_cursor = pages[cursor]
        return {"items": values, "nextCursor": next_cursor}

    assert await paginate_mcp_list(fetch, "items") == [1, 2, 3]
    assert cursors == [None, "next-a", "next-b"]


@pytest.mark.asyncio
async def test_pagination_stops_on_missing_cursor() -> None:
    async def fetch(cursor):
        return {"items": [1, 2]}

    assert await paginate_mcp_list(fetch, "items") == [1, 2]


@pytest.mark.asyncio
async def test_pagination_caps_total_items() -> None:
    async def fetch(cursor):
        n = cursor or 0
        return {"items": [n], "nextCursor": n + 1}

    out = await paginate_mcp_list(fetch, "items")
    assert len(out) == 10_000
