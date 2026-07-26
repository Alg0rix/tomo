"""clarify tool tests."""

from __future__ import annotations

import pytest

from app.runtime.tools.registry import execute, reset_registry


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_registry()
    yield
    reset_registry()


def test_clarify_returns_prefix() -> None:
    result = execute("clarify", {"question": "Which environment?"})
    assert result == "CLARIFY: Which environment?"


def test_clarify_empty_is_error() -> None:
    assert execute("clarify", {"question": "  "}).startswith("Error")


def test_clarify_missing_is_error() -> None:
    assert execute("clarify", {}).startswith("Error")
