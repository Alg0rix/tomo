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


def test_web_search_formats_results() -> None:
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "Heading": "Python",
        "AbstractText": "A programming language.",
        "AbstractURL": "https://python.org",
        "RelatedTopics": [
            {"Text": "Python Software Foundation", "FirstURL": "https://psf.org"}
        ],
    }
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.return_value = mock_resp

    with patch("app.runtime.tools.web_search.httpx.Client", return_value=mock_client):
        result = execute("web_search", {"query": "python"})
    assert "1. Python" in result
    assert "A programming language." in result
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
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"Heading": "", "AbstractText": "", "RelatedTopics": []}
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.return_value = mock_resp

    with patch("app.runtime.tools.web_search.httpx.Client", return_value=mock_client):
        result = execute("web_search", {"query": "zzzz"})
    assert result.startswith("No results")
