"""Discovery normalization: raw MCP SDK types -> ``mcp_items`` row shape."""

from __future__ import annotations

from mcp import types as mcp_types

from app.runtime.mcp.discovery import (
    normalize_prompt,
    normalize_resource,
    normalize_resource_template,
    normalize_tool,
)

_SERVER = {"id": "github"}


def test_normalize_tool_builds_openai_function_schema() -> None:
    raw = mcp_types.Tool(
        name="create_issue",
        description="Create a GitHub issue",
        inputSchema={"type": "object", "properties": {"title": {"type": "string"}}},
    )
    item = normalize_tool(_SERVER, raw)
    assert item["kind"] == "tool"
    assert item["runtime_id"] == "mcp__github__create_issue"
    assert item["name"] == "create_issue"
    assert item["schema"] == {
        "type": "function",
        "function": {
            "name": "mcp__github__create_issue",
            "description": "Create a GitHub issue",
            "parameters": {"type": "object", "properties": {"title": {"type": "string"}}},
        },
    }
    assert item["metadata"]["mcp_name"] == "create_issue"
    assert item["metadata"]["server_id"] == "github"


def test_normalize_tool_defaults_missing_schema_to_empty_object() -> None:
    raw = mcp_types.Tool(name="ping", inputSchema={})
    item = normalize_tool(_SERVER, raw)
    assert item["schema"]["function"]["parameters"] == {"type": "object", "properties": {}}
    # No description/title -> falls back to the tool name.
    assert item["description"] == "ping"


def test_normalize_tool_does_not_trust_annotations_for_safety() -> None:
    raw = mcp_types.Tool(
        name="danger",
        inputSchema={"type": "object"},
        annotations=mcp_types.ToolAnnotations(readOnlyHint=True),
    )
    item = normalize_tool(_SERVER, raw)
    # Annotations land only in metadata, never influence kind/schema/enablement.
    assert "annotations" in item["metadata"]
    assert item["kind"] == "tool"


def test_normalize_resource() -> None:
    raw = mcp_types.Resource(
        name="readme", uri="file:///readme.md", mimeType="text/markdown", description="d"
    )
    item = normalize_resource(_SERVER, raw)
    assert item["kind"] == "resource"
    assert item["uri"] == "file:///readme.md"
    assert item["mime_type"] == "text/markdown"
    assert item["runtime_id"] == ""


def test_normalize_resource_template() -> None:
    raw = mcp_types.ResourceTemplate(name="file", uriTemplate="file:///{path}")
    item = normalize_resource_template(_SERVER, raw)
    assert item["kind"] == "resource_template"
    assert item["uri"] == "file:///{path}"


def test_normalize_prompt_with_arguments() -> None:
    raw = mcp_types.Prompt(
        name="review_code",
        description="Review code",
        arguments=[mcp_types.PromptArgument(name="path", required=True)],
    )
    item = normalize_prompt(_SERVER, raw)
    assert item["kind"] == "prompt"
    assert item["schema"]["arguments"] == [
        {"name": "path", "description": "", "required": True}
    ]
