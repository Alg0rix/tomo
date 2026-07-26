"""forget_memory tool tests."""

from __future__ import annotations

import pytest

from app.runtime.tools.registry import execute, reset_registry
from app.services import store


@pytest.fixture(autouse=True)
def _reset(tmp_path) -> None:
    reset_registry()
    store.rebind(tmp_path / "forget.db")
    yield
    reset_registry()


def test_forget_memory_by_id() -> None:
    entry = store.create_knowledge_entry(
        {"title": "Temp Fact", "body": "forget me soon", "tags": ["tmp"]}
    )
    result = execute("forget_memory", {"id": entry["id"]})
    assert "Forgot" in result
    assert store.get_knowledge_entry(entry["id"]) is None


def test_forget_memory_by_query() -> None:
    entry = store.create_knowledge_entry(
        {"title": "UniqueZebraFact", "body": "zebra details", "tags": []}
    )
    result = execute("forget_memory", {"query": "UniqueZebraFact"})
    assert "Forgot" in result
    assert store.get_knowledge_entry(entry["id"]) is None


def test_forget_memory_unknown_id_is_error() -> None:
    assert execute("forget_memory", {"id": "nope"}).startswith("Error")


def test_forget_memory_requires_id_or_query() -> None:
    assert execute("forget_memory", {}).startswith("Error")
