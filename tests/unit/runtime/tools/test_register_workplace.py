"""register_workplace tool — local path auto-register."""

from __future__ import annotations

from pathlib import Path

from app.runtime.tools import register_workplace, sandbox
from app.runtime.tools.registry import reset_registry
from app.runtime.tools.workplace_ctx import current_workplace_id, reset_workplace
from app.services import store


def _rebind(tmp_path: Path) -> None:
    store.rebind(tmp_path / "reg_wp.db")
    reset_registry()
    sandbox.reset_agent()
    reset_workplace()


def test_register_local_and_bind(tmp_path: Path) -> None:
    _rebind(tmp_path)
    proj = tmp_path / "tomo-server"
    proj.mkdir()
    sandbox.bind_agent("ops")
    try:
        out = register_workplace.run(
            {"kind": "local", "path": str(proj), "name": "tomo-server"}
        )
        assert out.startswith("Registered workplace")
        assert current_workplace_id() is not None
        wps = store.list_workplaces()
        local = [
            w
            for w in wps
            if w.get("kind") == "local"
            and w.get("root_path") == str(proj.resolve())
        ]
        assert len(local) == 1
        agent = store.get_agent("ops")
        # ops starts as all_tunnels; local is not covered so allowlist expands.
        assert local[0]["id"] in (agent.get("workplace_ids") or []) or agent.get(
            "workplace_id"
        ) == local[0]["id"]
    finally:
        sandbox.reset_agent()
        reset_workplace()


def test_reuse_same_local_path(tmp_path: Path) -> None:
    _rebind(tmp_path)
    proj = tmp_path / "same"
    proj.mkdir()
    sandbox.bind_agent("main")
    try:
        out1 = register_workplace.run({"kind": "local", "path": str(proj)})
        out2 = register_workplace.run({"kind": "local", "path": str(proj)})
        assert "Registered" in out1
        assert "Reused" in out2
        locals_ = [
            w
            for w in store.list_workplaces()
            if w.get("kind") == "local"
            and str(proj.resolve()) in (w.get("root_path") or "")
        ]
        assert len(locals_) == 1
    finally:
        sandbox.reset_agent()
        reset_workplace()
