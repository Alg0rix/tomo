"""Knowledge entries: SQLite CRUD + keyword search (Slice E)."""

from __future__ import annotations

from pathlib import Path

from app.services import store


def _rebind(tmp_path: Path) -> None:
    store.rebind(tmp_path / "knowledge.db")


def test_migrate_creates_knowledge_entries_table(tmp_path: Path) -> None:
    _rebind(tmp_path)
    names = {
        r[0]
        for r in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "knowledge_entries" in names


def test_seed_includes_vendor_deadline(tmp_path: Path) -> None:
    _rebind(tmp_path)
    entries = store.list_knowledge_entries()
    ids = {e["id"] for e in entries}
    assert "kb_vendor_deadline" in ids
    hit = store.get_knowledge_entry("kb_vendor_deadline")
    assert hit is not None
    assert "October 15, 2026" in hit["body"]


def test_create_update_delete(tmp_path: Path) -> None:
    _rebind(tmp_path)
    created = store.create_knowledge_entry(
        {
            "id": "kb_test",
            "title": "Test fact",
            "body": "Hello knowledge",
            "tags": ["alpha", "test"],
        }
    )
    assert created["id"] == "kb_test"
    assert created["tags"] == ["alpha", "test"]
    updated = store.update_knowledge_entry(
        "kb_test", {"body": "Updated body", "tags": ["beta"]}
    )
    assert updated is not None
    assert updated["body"] == "Updated body"
    assert updated["tags"] == ["beta"]
    assert store.delete_knowledge_entry("kb_test") is True
    assert store.get_knowledge_entry("kb_test") is None


def test_create_auto_id(tmp_path: Path) -> None:
    _rebind(tmp_path)
    created = store.create_knowledge_entry(
        {"title": "Auto Id Fact", "body": "body"}
    )
    assert created["id"].startswith("kb_")
    assert "auto" in created["id"]


def test_search_finds_seeded_deadline(tmp_path: Path) -> None:
    _rebind(tmp_path)
    hits = store.search_knowledge("vendor onboarding deadline")
    assert hits
    assert any("October 15, 2026" in h["body"] for h in hits)


def test_search_empty_query(tmp_path: Path) -> None:
    _rebind(tmp_path)
    assert store.search_knowledge("   ") == []
