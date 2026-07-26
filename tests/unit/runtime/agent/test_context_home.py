"""System prompt resolution from $TOMO_HOME SOUL/SYSTEM (Alpha Slice 0).

Covers :func:`build_system_prompt`: agent ``SYSTEM.md`` overrides the repo
default base; global ``SOUL.md`` is prepended; agent ``SOUL.md`` is appended as
an overlay; a missing agent falls back to the repo default.
"""

from __future__ import annotations

from pathlib import Path

from app.core import home
from app.runtime.agent.context import build_system_prompt


def test_build_system_prompt_uses_soul_and_system(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("TOMO_SECRET_KEY", raising=False)
    home.ensure_tomo_home(tmp_path)
    (tmp_path / "SOUL.md").write_text("PERSONA: concise.\n", encoding="utf-8")
    agent = tmp_path / "agents" / "ops"
    agent.mkdir(parents=True)
    (agent / "SYSTEM.md").write_text("You are Ops.\n", encoding="utf-8")

    text = build_system_prompt("ops", home_root=tmp_path)
    assert "PERSONA: concise" in text
    assert "You are Ops" in text


def test_missing_agent_falls_back_to_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("TOMO_SECRET_KEY", raising=False)
    home.ensure_tomo_home(tmp_path)
    text = build_system_prompt("ghost", home_root=tmp_path)
    assert len(text) > 20  # repo default or fallback


def test_agent_soul_overlay_appended_after_base(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("TOMO_SECRET_KEY", raising=False)
    home.ensure_tomo_home(tmp_path)
    (tmp_path / "SOUL.md").write_text("GLOBAL PERSONA.\n", encoding="utf-8")
    agent = tmp_path / "agents" / "ops"
    agent.mkdir(parents=True)
    (agent / "SYSTEM.md").write_text("Base instructions.\n", encoding="utf-8")
    (agent / "SOUL.md").write_text("AGENT PERSONA OVERLAY.\n", encoding="utf-8")

    text = build_system_prompt("ops", home_root=tmp_path)
    assert "GLOBAL PERSONA" in text
    assert "Base instructions" in text
    assert "AGENT PERSONA OVERLAY" in text
    # global persona prepended; base next; overlay appended last
    assert text.index("GLOBAL PERSONA") < text.index("Base instructions")
    assert text.index("Base instructions") < text.index("AGENT PERSONA OVERLAY")


def test_no_agent_uses_global_soul_and_default_system(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("TOMO_SECRET_KEY", raising=False)
    home.ensure_tomo_home(tmp_path)
    (tmp_path / "SOUL.md").write_text("ONLY PERSONA.\n", encoding="utf-8")

    text = build_system_prompt(None, home_root=tmp_path)
    assert "ONLY PERSONA" in text
    # falls back to repo default coordinator system prompt
    assert "Tomo" in text


def test_empty_agent_system_falls_back_to_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("TOMO_SECRET_KEY", raising=False)
    home.ensure_tomo_home(tmp_path)
    agent = tmp_path / "agents" / "ops"
    agent.mkdir(parents=True)
    (agent / "SYSTEM.md").write_text("   \n\n  \n", encoding="utf-8")  # blank

    text = build_system_prompt("ops", home_root=tmp_path)
    # blank agent SYSTEM.md -> repo default base is used
    assert "Tomo" in text
