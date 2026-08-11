"""Tomo Browser Control — client-tool executor over Chrome extension.

Agent tools call into :mod:`app.runtime.browser.gateway`, which dispatches
execution to the authenticated user's browser via WebSocket → Tomo web →
Chrome extension → ``chrome.debugger`` (CDP).

Public surface:

* :func:`get_gateway` — process-wide :class:`BrowserGateway`
* :data:`BROWSER_TOOL_NAMES` — tools owned by this package
* :func:`filter_tools_for_browser` — capability-aware schema filter
"""

from __future__ import annotations

from app.runtime.browser.gateway import BrowserGateway, get_gateway
from app.runtime.browser.tools_meta import (
    BROWSER_TOOL_NAMES,
    CAPABILITY_BY_TOOL,
    READ_ONLY_BROWSER_TOOLS,
    filter_tools_for_browser,
    tools_for_capabilities,
)

__all__ = [
    "BrowserGateway",
    "BROWSER_TOOL_NAMES",
    "CAPABILITY_BY_TOOL",
    "READ_ONLY_BROWSER_TOOLS",
    "filter_tools_for_browser",
    "get_gateway",
    "tools_for_capabilities",
]
