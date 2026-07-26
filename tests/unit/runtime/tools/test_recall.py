"""recall / remember tool backends (Slice E)."""

from __future__ import annotations

from pathlib import Path

from app.runtime.tools.registry import ToolRegistry, execute, get_openai_tools, reset_registry
from app.services import store


def _rebind(tmp_path: Path) -> None:
    store.rebind(tmp_path / "recall_tools.db")
    reset_registry()


def test_registry_loads_recall_and_remember() -> None:
    names = ToolRegistry().names()
    assert "recall" in names
    assert "remember" in names
    tools = get_openai_tools()
    recall = next(t for t in tools if t["function"]["name"] == "recall")
    assert "query" in recall["function"]["parameters"]["properties"]


def test_execute_recall_returns_seeded_fact(tmp_path: Path) -> None:
    _rebind(tmp_path)
    result = execute("recall", {"query": "vendor deadline"})
    assert "October 15, 2026" in result
    assert "Q3 vendor" in result or "vendor" in result.lower()


def test_execute_recall_no_match(tmp_path: Path) -> None:
    _rebind(tmp_path)
    result = execute("recall", {"query": "zzzznonexistentxyz"})
    assert result.startswith("No knowledge entries matched")


def test_execute_recall_bad_query() -> None:
    assert execute("recall", {"query": ""}).startswith("Error")
    assert execute("recall", {}).startswith("Error")


def test_execute_remember_then_recall(tmp_path: Path) -> None:
    _rebind(tmp_path)
    saved = execute(
        "remember",
        {
            "title": "Secret snack",
            "body": "The office snack code is pretzel-42.",
            "tags": ["snack", "office"],
        },
    )
    assert saved.startswith("Saved knowledge entry")
    result = execute("recall", {"query": "pretzel snack"})
    assert "pretzel-42" in result
