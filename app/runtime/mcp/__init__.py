"""MCP client runtime: stable IDs, discovery normalization, and live sessions."""

from __future__ import annotations

from app.runtime.mcp.discovery import (
    normalize_prompt,
    normalize_resource,
    normalize_resource_template,
    normalize_tool,
    paginate_mcp_list,
)
from app.runtime.mcp.manager import McpConnectionManager, mcp_manager
from app.runtime.mcp.names import is_mcp_runtime_id, runtime_tool_id, split_runtime_tool_id
from app.runtime.mcp.results import (
    render_prompt_result,
    render_resource_result,
    render_tool_result,
)

__all__ = [
    "runtime_tool_id",
    "split_runtime_tool_id",
    "is_mcp_runtime_id",
    "paginate_mcp_list",
    "normalize_tool",
    "normalize_resource",
    "normalize_resource_template",
    "normalize_prompt",
    "render_tool_result",
    "render_resource_result",
    "render_prompt_result",
    "McpConnectionManager",
    "mcp_manager",
]
