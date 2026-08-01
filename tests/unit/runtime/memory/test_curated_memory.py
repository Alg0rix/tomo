"""Curated MEMORY.md / USER.md memory store."""

from __future__ import annotations

from pathlib import Path

from app.core import config, home
from app.runtime.memory import curated
from app.runtime.tools import memory as memory_tool
from app.runtime.tools import sandbox


def test_add_list_user_and_agent_memory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "TOMO_HOME", tmp_path)
    home.ensure_tomo_home(tmp_path)
    curated.reset_freeze()

    out = curated.add_entry("user", "Prefers short answers.", agent_id="main")
    assert out["ok"] is True
    assert home.user_memory_path(tmp_path).is_file()

    out2 = curated.add_entry(
        "memory", "Staging host is staging.tomo.internal", agent_id="ops"
    )
    assert out2["ok"] is True
    path = home.agent_memory_path("ops", tmp_path)
    assert path.is_file()
    assert "Staging host" in path.read_text(encoding="utf-8")

    listed = curated.list_entries("memory", agent_id="ops", home_root=tmp_path)
    assert listed["count"] == 1


def test_frozen_snapshot_ignores_mid_session_writes(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(config, "TOMO_HOME", tmp_path)
    home.ensure_tomo_home(tmp_path)
    curated.reset_freeze()
    curated.add_entry("user", "Timezone: Asia/Jakarta", agent_id="main", home_root=tmp_path)

    first = curated.prompt_block("main", session_id="sess1", home_root=tmp_path)
    assert "Asia/Jakarta" in first

    curated.add_entry("user", "Likes bullet lists", agent_id="main", home_root=tmp_path)
    second = curated.prompt_block("main", session_id="sess1", home_root=tmp_path)
    assert second == first
    assert "bullet" not in second

    # New session sees both
    third = curated.prompt_block("main", session_id="sess2", home_root=tmp_path)
    assert "Asia/Jakarta" in third
    assert "bullet" in third


def test_build_system_prompt_includes_memory_guidance(
    tmp_path: Path, monkeypatch
) -> None:
    from app.runtime.agent.context import build_system_prompt

    monkeypatch.delenv("TOMO_SECRET_KEY", raising=False)
    monkeypatch.setattr(config, "TOMO_HOME", tmp_path)
    home.ensure_tomo_home(tmp_path)
    curated.reset_freeze()
    text = build_system_prompt("main", home_root=tmp_path, session_id="s1")
    assert "proactively" in text.lower()
    assert "memory" in text.lower()


def test_build_system_prompt_includes_curated_memory(
    tmp_path: Path, monkeypatch
) -> None:
    from app.runtime.agent.context import build_system_prompt

    monkeypatch.delenv("TOMO_SECRET_KEY", raising=False)
    monkeypatch.setattr(config, "TOMO_HOME", tmp_path)
    home.ensure_tomo_home(tmp_path)
    curated.reset_freeze()
    curated.add_entry("user", "Name is Alex", agent_id="main", home_root=tmp_path)

    text = build_system_prompt("main", home_root=tmp_path, session_id="s1")
    assert "USER PROFILE" in text
    assert "Name is Alex" in text


def test_memory_tool_add(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "TOMO_HOME", tmp_path)
    home.ensure_tomo_home(tmp_path)
    curated.reset_freeze()
    sandbox.bind_agent("ops")
    try:
        msg = memory_tool.run(
            {"action": "add", "target": "memory", "content": "VPN needs MFA"}
        )
        assert msg.startswith("added") or "added" in msg.lower()
        assert "VPN needs MFA" in home.agent_memory_path("ops", tmp_path).read_text(
            encoding="utf-8"
        )
    finally:
        sandbox.reset_agent()


def test_replace_and_remove(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "TOMO_HOME", tmp_path)
    home.ensure_tomo_home(tmp_path)
    curated.reset_freeze()
    curated.add_entry("memory", "Port is 8080", agent_id="coder", home_root=tmp_path)
    r = curated.replace_entry(
        "memory", "8080", "Port is 9090", agent_id="coder", home_root=tmp_path
    )
    assert r["ok"] is True
    assert "9090" in home.agent_memory_path("coder", tmp_path).read_text(encoding="utf-8")
    r2 = curated.remove_entry(
        "memory", "9090", agent_id="coder", home_root=tmp_path
    )
    assert r2["ok"] is True
    assert curated.list_entries("memory", agent_id="coder", home_root=tmp_path)[
        "count"
    ] == 0
