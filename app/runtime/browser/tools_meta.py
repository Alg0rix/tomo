"""Browser tool catalog metadata — names, capabilities, risk, filtering."""

from __future__ import annotations

from typing import Any, Iterable

# Tool name → required capability key.
CAPABILITY_BY_TOOL: dict[str, str] = {
    "browser_tabs": "browser.tabs",
    "browser_attach": "browser.attach",
    "browser_snapshot": "browser.snapshot",
    "browser_click": "browser.click",
    "browser_type": "browser.type",
    "browser_press": "browser.press",
    "browser_select": "browser.select",
    "browser_scroll": "browser.scroll",
    "browser_navigate": "browser.navigate",
    "browser_back": "browser.back",
    "browser_forward": "browser.forward",
    "browser_wait": "browser.wait",
    "browser_screenshot": "browser.screenshot",
    "browser_extract": "browser.extract",
}

BROWSER_TOOL_NAMES: frozenset[str] = frozenset(CAPABILITY_BY_TOOL)

# Safe for parallel execution within a tool round.
READ_ONLY_BROWSER_TOOLS: frozenset[str] = frozenset(
    {
        "browser_tabs",
        "browser_snapshot",
        "browser_screenshot",
        "browser_extract",
        "browser_wait",
    }
)

# Risk classification (design §22).
RISK_BY_TOOL: dict[str, str] = {
    "browser_tabs": "read",
    "browser_attach": "read",
    "browser_snapshot": "read",
    "browser_screenshot": "read",
    "browser_extract": "read",
    "browser_wait": "read",
    "browser_click": "interaction",
    "browser_type": "interaction",
    "browser_press": "interaction",
    "browser_select": "interaction",
    "browser_scroll": "interaction",
    "browser_navigate": "interaction",
    "browser_back": "interaction",
    "browser_forward": "interaction",
}


def is_browser_tool(name: str) -> bool:
    return name in BROWSER_TOOL_NAMES


def tools_for_capabilities(capabilities: Iterable[str]) -> list[str]:
    """Return browser tool names supported by the given capability set."""
    caps = set(capabilities)
    return [
        name
        for name, cap in CAPABILITY_BY_TOOL.items()
        if cap in caps
    ]


def filter_tools_for_browser(
    tools: list[dict[str, Any]],
    *,
    connected: bool,
    capabilities: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Strip or capability-filter browser tools from OpenAI tool schemas.

    When the browser is not connected, no browser tools are advertised.
    When connected, only tools whose capability is negotiated remain.
    """
    if not connected:
        return [
            t
            for t in tools
            if (t.get("function") or {}).get("name") not in BROWSER_TOOL_NAMES
        ]
    allow = set(tools_for_capabilities(capabilities or ()))
    out: list[dict[str, Any]] = []
    for t in tools:
        name = (t.get("function") or {}).get("name")
        if name in BROWSER_TOOL_NAMES and name not in allow:
            continue
        out.append(t)
    return out


def risk_for(tool: str) -> str:
    return RISK_BY_TOOL.get(tool, "interaction")
