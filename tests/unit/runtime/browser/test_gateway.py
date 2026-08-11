"""Browser gateway + dynamic tool filtering unit tests."""

from __future__ import annotations

import asyncio
import concurrent.futures

import pytest

from app.runtime.browser.gateway import (
    BrowserClientLink,
    BrowserGateway,
    result_to_tool_text,
)
from app.runtime.browser.protocol import TYPE_TOOL_RESULT, envelope
from app.runtime.browser.session import BrowserSessionStore, reset_session_store
from app.runtime.browser.tools_meta import (
    BROWSER_TOOL_NAMES,
    filter_tools_for_browser,
    tools_for_capabilities,
)
from app.runtime.browser.gateway import reset_gateway


@pytest.fixture(autouse=True)
def _clean_browser_state():
    reset_session_store()
    reset_gateway()
    yield
    reset_session_store()
    reset_gateway()


def test_tools_for_capabilities_subset():
    names = tools_for_capabilities(["browser.tabs", "browser.snapshot"])
    assert "browser_tabs" in names
    assert "browser_snapshot" in names
    assert "browser_click" not in names


def test_filter_tools_disconnected_hides_browser():
    tools = [
        {"type": "function", "function": {"name": "bash", "parameters": {}}},
        {"type": "function", "function": {"name": "browser_tabs", "parameters": {}}},
        {"type": "function", "function": {"name": "browser_click", "parameters": {}}},
    ]
    out = filter_tools_for_browser(tools, connected=False)
    names = [(t.get("function") or {}).get("name") for t in out]
    assert names == ["bash"]


def test_filter_tools_connected_capability_aware():
    tools = [
        {"type": "function", "function": {"name": "bash", "parameters": {}}},
        {"type": "function", "function": {"name": "browser_tabs", "parameters": {}}},
        {"type": "function", "function": {"name": "browser_click", "parameters": {}}},
    ]
    out = filter_tools_for_browser(
        tools, connected=True, capabilities=["browser.tabs"]
    )
    names = [(t.get("function") or {}).get("name") for t in out]
    assert "bash" in names
    assert "browser_tabs" in names
    assert "browser_click" not in names


def test_session_store_create_and_tabs():
    store = BrowserSessionStore()
    s = store.create(
        user_id="u1",
        client_id="c1",
        capabilities=["browser.tabs", "browser.snapshot"],
    )
    assert s.id.startswith("brs_")
    assert "browser.tabs" in s.capabilities
    s.set_tabs(
        [
            {
                "id": "tab_abc",
                "title": "GitHub",
                "url": "https://github.com/x",
            }
        ]
    )
    assert "tab_abc" in s.authorized_tabs
    assert s.authorized_tabs["tab_abc"].domain == "github.com"
    got = store.get_for_user("u1")
    assert got is not None and got.id == s.id


def test_gateway_execute_without_link_returns_disconnected():
    gw = BrowserGateway()
    result = gw.execute("browser_tabs", {}, user_id="nobody")
    assert result["success"] is False
    assert result["error"]["code"] == "BROWSER_DISCONNECTED"


def test_gateway_pending_call_roundtrip():
    """Simulates client answering a tool.execute with tool.result."""
    import threading
    import time

    store = BrowserSessionStore()
    session = store.create(user_id="u1", client_id="c1")
    gw = BrowserGateway()

    class FakeWS:
        def __init__(self):
            self.sent = []

        async def send_json(self, msg):
            self.sent.append(msg)

    loop = asyncio.new_event_loop()

    def _run_loop():
        asyncio.set_event_loop(loop)
        loop.run_forever()

    t = threading.Thread(target=_run_loop, daemon=True)
    t.start()
    ws = FakeWS()
    link = BrowserClientLink(session, ws, loop)  # type: ignore[arg-type]
    gw.register_link(link)

    def client_responder():
        for _ in range(100):
            if ws.sent:
                break
            time.sleep(0.02)
        assert ws.sent, "gateway never sent execute"
        msg = ws.sent[0]
        call_id = msg.get("call_id") or (msg.get("payload") or {}).get("call_id")
        result = {
            "success": True,
            "tabs": [{"id": "tab_1", "title": "T", "url": "https://ex.com"}],
        }
        gw.handle_client_message(
            session.id,
            {
                "type": TYPE_TOOL_RESULT,
                "call_id": call_id,
                "result": result,
            },
        )

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    pool.submit(client_responder)
    try:
        out = gw.execute("browser_tabs", {}, user_id="u1", timeout=5.0)
        assert out["success"] is True
        assert out["tabs"][0]["id"] == "tab_1"
    finally:
        pool.shutdown(wait=False)
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2.0)
        loop.close()


def test_result_to_tool_text_snapshot_and_error():
    text = result_to_tool_text(
        {
            "success": True,
            "tab": {"id": "tab_1", "title": "Hi", "url": "https://x"},
            "snapshot": "PAGE\n...",
        }
    )
    assert "tab_1" in text
    assert "PAGE" in text
    err = result_to_tool_text(
        {
            "success": False,
            "error": {
                "code": "STALE_ELEMENT",
                "message": "stale",
                "suggested_action": "browser_snapshot",
            },
        }
    )
    assert "STALE_ELEMENT" in err
    assert "browser_snapshot" in err


def test_browser_tool_names_cover_mvp():
    for name in (
        "browser_tabs",
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_navigate",
        "browser_screenshot",
    ):
        assert name in BROWSER_TOOL_NAMES


def test_registry_loads_browser_tools():
    from app.runtime.tools.registry import ToolRegistry

    reg = ToolRegistry()
    assert "browser_snapshot" in reg.names()
    assert "browser_click" in reg.names()
