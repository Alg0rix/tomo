"""Bounded rendering of MCP tool/resource/prompt results."""

from __future__ import annotations

from mcp import types as mcp_types

from app.runtime.mcp.results import (
    render_prompt_result,
    render_resource_result,
    render_tool_result,
)


def test_render_tool_result_text() -> None:
    result = mcp_types.CallToolResult(content=[mcp_types.TextContent(type="text", text="hi")])
    assert render_tool_result(result) == "hi"


def test_render_tool_result_error_prefixed() -> None:
    result = mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text="bad")], isError=True
    )
    out = render_tool_result(result)
    assert out.startswith("Error:")
    assert "bad" in out


def test_render_tool_result_image_is_bounded_summary_not_base64() -> None:
    result = mcp_types.CallToolResult(
        content=[mcp_types.ImageContent(type="image", data="A" * 500, mimeType="image/png")]
    )
    out = render_tool_result(result)
    assert "A" * 500 not in out
    assert "image/png" in out
    assert "500" in out


def test_render_tool_result_truncates_at_max_chars() -> None:
    result = mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text="x" * 100)]
    )
    out = render_tool_result(result, max_chars=10)
    assert len(out) < 100
    assert "truncated" in out


def test_render_resource_result_text_and_blob() -> None:
    result = mcp_types.ReadResourceResult(
        contents=[
            mcp_types.TextResourceContents(uri="file:///a.txt", text="hello", mimeType="text/plain"),
            mcp_types.BlobResourceContents(uri="file:///a.png", blob="A" * 40, mimeType="image/png"),
        ]
    )
    out = render_resource_result(result)
    assert out["contents"][0]["kind"] == "text"
    assert out["contents"][0]["text"] == "hello"
    assert out["contents"][1]["kind"] == "blob"
    assert out["contents"][1]["size_base64_chars"] == 40
    assert "A" * 40 not in str(out)  # raw blob bytes never embedded


def test_render_prompt_result_flattens_messages() -> None:
    result = mcp_types.GetPromptResult(
        description="d",
        messages=[
            mcp_types.PromptMessage(
                role="user", content=mcp_types.TextContent(type="text", text="do it")
            )
        ],
    )
    out = render_prompt_result(result)
    assert out["description"] == "d"
    assert out["messages"] == [{"role": "user", "text": "do it"}]
