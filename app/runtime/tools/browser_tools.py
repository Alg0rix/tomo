"""Browser client tools — dispatch to BrowserGateway (Chrome extension).

All tools share the same shape: validate args, call gateway.execute, return
agent-facing string. Native CDP details never surface here.
"""

from __future__ import annotations

from typing import Any

from app.runtime.browser.context import (
    current_browser_agent_id,
    current_browser_chat_session,
    current_browser_user_id,
)
from app.runtime.browser.gateway import get_gateway, result_to_tool_text


def _run(tool: str, arguments: dict[str, Any]) -> str:
    if not isinstance(arguments, dict):
        return "Error: arguments must be an object"
    gw = get_gateway()
    result = gw.execute(
        tool,
        arguments,
        user_id=current_browser_user_id(),
        agent_id=current_browser_agent_id() or "",
        conversation_id=current_browser_chat_session() or "",
    )
    return result_to_tool_text(result)


def browser_tabs(arguments: dict[str, Any]) -> str:
    return _run("browser_tabs", arguments if isinstance(arguments, dict) else {})


def browser_attach(arguments: dict[str, Any]) -> str:
    return _run("browser_attach", arguments if isinstance(arguments, dict) else {})


def browser_snapshot(arguments: dict[str, Any]) -> str:
    return _run("browser_snapshot", arguments if isinstance(arguments, dict) else {})


def browser_click(arguments: dict[str, Any]) -> str:
    return _run("browser_click", arguments if isinstance(arguments, dict) else {})


def browser_type(arguments: dict[str, Any]) -> str:
    return _run("browser_type", arguments if isinstance(arguments, dict) else {})


def browser_press(arguments: dict[str, Any]) -> str:
    return _run("browser_press", arguments if isinstance(arguments, dict) else {})


def browser_select(arguments: dict[str, Any]) -> str:
    return _run("browser_select", arguments if isinstance(arguments, dict) else {})


def browser_scroll(arguments: dict[str, Any]) -> str:
    return _run("browser_scroll", arguments if isinstance(arguments, dict) else {})


def browser_navigate(arguments: dict[str, Any]) -> str:
    return _run("browser_navigate", arguments if isinstance(arguments, dict) else {})


def browser_back(arguments: dict[str, Any]) -> str:
    return _run("browser_back", arguments if isinstance(arguments, dict) else {})


def browser_forward(arguments: dict[str, Any]) -> str:
    return _run("browser_forward", arguments if isinstance(arguments, dict) else {})


def browser_wait(arguments: dict[str, Any]) -> str:
    return _run("browser_wait", arguments if isinstance(arguments, dict) else {})


def browser_screenshot(arguments: dict[str, Any]) -> str:
    return _run("browser_screenshot", arguments if isinstance(arguments, dict) else {})


def browser_extract(arguments: dict[str, Any]) -> str:
    return _run("browser_extract", arguments if isinstance(arguments, dict) else {})


# Alias map for registry registration
BACKENDS: dict[str, Any] = {
    "browser_tabs": browser_tabs,
    "browser_attach": browser_attach,
    "browser_snapshot": browser_snapshot,
    "browser_click": browser_click,
    "browser_type": browser_type,
    "browser_press": browser_press,
    "browser_select": browser_select,
    "browser_scroll": browser_scroll,
    "browser_navigate": browser_navigate,
    "browser_back": browser_back,
    "browser_forward": browser_forward,
    "browser_wait": browser_wait,
    "browser_screenshot": browser_screenshot,
    "browser_extract": browser_extract,
}
