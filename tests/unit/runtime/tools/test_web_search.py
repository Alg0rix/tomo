"""web_search tool tests (httpx mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.runtime.tools.registry import execute, reset_registry


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_registry()
    yield
    reset_registry()


def _client_with_responses(*responses: MagicMock) -> MagicMock:
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.side_effect = list(responses)
    return mock_client


def test_web_search_formats_html_results() -> None:
    html = """
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fpython.org">Python</a>
    <a class="result__snippet">A programming language.</a>
    <a class="result__a" href="https://psf.org">Python Software Foundation</a>
    <a class="result__snippet">The PSF home.</a>
    """
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.text = html
    mock_client = _client_with_responses(mock_resp)

    with patch("app.runtime.tools.web_search.httpx.Client", return_value=mock_client):
        result = execute("web_search", {"query": "python"})
    assert "1. Python" in result
    assert "A programming language." in result
    assert "https://python.org" in result
    assert "Python Software Foundation" in result


def test_web_search_empty_query_is_error() -> None:
    assert execute("web_search", {"query": "  "}).startswith("Error")


def test_web_search_request_failure() -> None:
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.side_effect = httpx.TimeoutException("slow")

    with patch("app.runtime.tools.web_search.httpx.Client", return_value=mock_client):
        result = execute("web_search", {"query": "python"})
    assert result.startswith("Error")


def test_web_search_no_results() -> None:
    html_resp = MagicMock()
    html_resp.raise_for_status = MagicMock()
    html_resp.text = "<html><body>no hits</body></html>"

    ia_resp = MagicMock()
    ia_resp.raise_for_status = MagicMock()
    ia_resp.text = '{"Heading":"","AbstractText":"","RelatedTopics":[]}'
    ia_resp.json.return_value = {
        "Heading": "",
        "AbstractText": "",
        "RelatedTopics": [],
    }

    mock_client = _client_with_responses(html_resp, ia_resp)

    with patch("app.runtime.tools.web_search.httpx.Client", return_value=mock_client):
        result = execute("web_search", {"query": "zzzz"})
    assert result.startswith("No results")


def test_web_search_empty_ia_body_falls_through() -> None:
    """Instant Answer empty body must not raise JSON decode errors."""
    html_resp = MagicMock()
    html_resp.raise_for_status = MagicMock()
    html_resp.text = ""

    ia_resp = MagicMock()
    ia_resp.raise_for_status = MagicMock()
    ia_resp.text = ""
    ia_resp.json.side_effect = ValueError(
        "Expecting value: line 1 column 1 (char 0)"
    )

    mock_client = _client_with_responses(html_resp, ia_resp)

    with patch("app.runtime.tools.web_search.httpx.Client", return_value=mock_client):
        result = execute(
            "web_search",
            {
                "query": "Bank BCA developer API mutasi rekening statement transaction API"
            },
        )
    assert result.startswith("No results")
    assert "Expecting value" not in result


def test_web_search_falls_back_to_instant_answer() -> None:
    html_resp = MagicMock()
    html_resp.raise_for_status = MagicMock()
    html_resp.text = "<html></html>"

    ia_resp = MagicMock()
    ia_resp.raise_for_status = MagicMock()
    ia_resp.text = '{"ok":true}'
    ia_resp.json.return_value = {
        "Heading": "Python",
        "AbstractText": "A programming language.",
        "AbstractURL": "https://python.org",
        "RelatedTopics": [],
    }

    mock_client = _client_with_responses(html_resp, ia_resp)

    with patch("app.runtime.tools.web_search.httpx.Client", return_value=mock_client):
        result = execute("web_search", {"query": "python"})
    assert "1. Python" in result
    assert "A programming language." in result
