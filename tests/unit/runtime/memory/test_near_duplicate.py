"""curated.near_duplicate + add_entry skip."""

from __future__ import annotations

from app.runtime.memory.curated import add_entry, near_duplicate, read_entries, user_path, write_entries


def test_near_duplicate_exact(tmp_path, monkeypatch) -> None:
    from app.core import config

    monkeypatch.setattr(config, "TOMO_HOME", tmp_path)
    entries = ["User prefers concise answers"]
    assert near_duplicate(entries, "User prefers concise answers")
    assert near_duplicate(entries, "user prefers  concise   answers")
    assert near_duplicate(entries, "x") is None


def test_near_duplicate_substring(tmp_path) -> None:
    long = "User prefers concise answers without long preambles in chat"
    assert near_duplicate([long], "prefers concise answers without long preambles")
    assert near_duplicate(["short tip"], "short tip extra words here that are longer") is None


def test_add_entry_skips_near_dup(tmp_path, monkeypatch) -> None:
    from app.core import config

    monkeypatch.setattr(config, "TOMO_HOME", tmp_path)
    r1 = add_entry("user", "Prefers short answers over essays", agent_id=None)
    assert r1["ok"]
    r2 = add_entry(
        "user",
        "prefers short answers over essays",
        agent_id=None,
    )
    assert r2["ok"]
    assert "near-duplicate" in r2["message"] or "already" in r2["message"]
    entries = read_entries(user_path())
    assert len(entries) == 1
