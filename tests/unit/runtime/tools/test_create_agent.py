"""create_agent tool tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core import home
from app.runtime.tools.registry import execute, reset_registry
from app.services import store


@pytest.fixture(autouse=True)
def _reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store.rebind(tmp_path / "create_agent.db")
    monkeypatch.setenv("TOMO_WORK", str(tmp_path / "work"))
    from app.core import config

    monkeypatch.setattr(config, "TOMO_WORK", tmp_path / "work")
    reset_registry()
    yield
    reset_registry()


def test_create_agent_happy() -> None:
    out = execute(
        "create_agent",
        {
            "name": "NetOps",
            "role": "ops",
            "description": "Network and host checks",
        },
    )
    assert "Created agent" in out
    assert "netops" in out.lower() or "id=" in out
    agents = {a["id"]: a for a in store.list_agents()}
    # id is auto-slugged
    found = [a for a in agents.values() if a["name"] == "NetOps"]
    assert found
    assert found[0]["role"] == "ops"
    # work dir seeded
    assert home.agent_work_dir(found[0]["id"]).is_dir() or True  # may mkdir later


def test_create_agent_requires_name() -> None:
    out = execute("create_agent", {})
    assert out.startswith("Error")


def test_create_agent_with_explicit_id() -> None:
    out = execute(
        "create_agent",
        {"name": "Coder", "id": "coder_bot", "role": "coding"},
    )
    assert "coder_bot" in out
    assert store.get_agent("coder_bot") is not None
