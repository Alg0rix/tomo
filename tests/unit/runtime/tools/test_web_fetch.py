"""web_fetch tool tests (httpx mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.runtime.tools import web_fetch
from app.runtime.tools.registry import execute, reset_registry


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_registry()
    yield
    reset_registry()


def test_web_fetch_returns_text(monkeypatch) -> None:
    monkeypatch.setattr(web_fetch, "_is_blocked_host", lambda host: None)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {}
    mock_resp.text = "hello from web"
    mock_resp.raise_for_status = MagicMock()
    mock_resp.url = "https://example.com/page"

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.return_value = mock_resp

    with patch("app.runtime.tools.web_fetch.httpx.Client", return_value=mock_client):
        result = execute("web_fetch", {"url": "https://example.com/page"})
    assert result == "hello from web"


def test_web_fetch_extracts_main_text_before_truncating_html(monkeypatch) -> None:
    """Large page chrome must not hide the useful document content."""
    monkeypatch.setattr(web_fetch, "_is_blocked_host", lambda host: None)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "text/html; charset=utf-8"}
    mock_resp.text = (
        "<html><head><title>Mermaid configuration</title>"
        f"<script>{'x' * (web_fetch._MAX_CHARS + 1)}</script></head>"
        "<body><nav>Navigation noise</nav><main>"
        "<h1>Using the Mermaid configuration</h1>"
        "<p>Set securityLevel to strict or loose.</p>"
        "</main></body></html>"
    )
    mock_resp.raise_for_status = MagicMock()
    mock_resp.url = "https://mermaid.js.org/config/usage.html"

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.return_value = mock_resp

    with patch("app.runtime.tools.web_fetch.httpx.Client", return_value=mock_client):
        result = execute(
            "web_fetch", {"url": "https://mermaid.js.org/config/usage.html"}
        )

    assert "Using the Mermaid configuration" in result
    assert "securityLevel" in result
    assert "Navigation noise" not in result
    assert "x" * 100 not in result
    assert "truncated" not in result


def test_web_fetch_blocks_loopback() -> None:
    result = execute("web_fetch", {"url": "http://127.0.0.1/"})
    assert result.startswith("Error")
    assert "blocked" in result.lower() or "private" in result.lower() or "loopback" in result.lower()


def test_web_fetch_blocks_redirect_to_loopback(monkeypatch) -> None:
    monkeypatch.setattr(
        web_fetch,
        "_is_blocked_host",
        lambda host: (
            "Error: blocked"
            if host in {"127.0.0.1", "localhost"}
            else None
        ),
    )

    redirect = httpx.Response(
        302,
        headers={"location": "http://127.0.0.1/secret"},
        request=httpx.Request("GET", "https://example.com/go"),
    )

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.return_value = redirect

    with patch("app.runtime.tools.web_fetch.httpx.Client", return_value=mock_client):
        result = execute("web_fetch", {"url": "https://example.com/go"})
    assert result.startswith("Error")
    assert "blocked" in result.lower() or "private" in result.lower() or "loopback" in result.lower()


def test_web_fetch_missing_url_is_error() -> None:
    assert execute("web_fetch", {}).startswith("Error")


def test_web_fetch_http_error(monkeypatch) -> None:
    monkeypatch.setattr(web_fetch, "_is_blocked_host", lambda host: None)
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.side_effect = httpx.ConnectError("boom")

    with patch("app.runtime.tools.web_fetch.httpx.Client", return_value=mock_client):
        result = execute("web_fetch", {"url": "https://example.com/"})
    assert result.startswith("Error")
