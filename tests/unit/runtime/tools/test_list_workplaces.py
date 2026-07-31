"""list_workplaces tool — registry catalog, not filesystem."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.runtime.tools import sandbox
from app.runtime.tools.registry import execute, reset_registry
from app.services import store


@pytest.fixture(autouse=True)
def _db(tmp_path: Path) -> None:
    reset_registry()
    store.rebind(tmp_path / "list_wp.db")
    sandbox.reset_agent()
    yield
    sandbox.reset_agent()
    reset_registry()


def test_list_workplaces_ops_all_tunnels(tmp_path: Path) -> None:
    store.create_workplace(
        {
            "id": "tun_second",
            "name": "SECOND-DEV",
            "kind": "tunnel",
        }
    )
    store.create_workplace(
        {
            "id": "wp_local",
            "name": "local-only",
            "kind": "local",
            "root_path": str(tmp_path),
        }
    )
    store.update_agent("ops", {"workplace_scope": "all_tunnels"})
    sandbox.bind_agent("ops")
    out = execute("list_workplaces", {})
    assert "SECOND-DEV" in out
    assert "tun_second" in out
    assert "local-only" not in out
    assert "Do not discover workplaces via filesystem" in out


def test_list_workplaces_coordinator_sees_all(tmp_path: Path) -> None:
    store.create_workplace({"id": "tun_a", "name": "alpha", "kind": "tunnel"})
    store.create_workplace(
        {
            "id": "wp_b",
            "name": "beta-local",
            "kind": "local",
            "root_path": str(tmp_path),
        }
    )
    sandbox.bind_agent("main")
    out = execute("list_workplaces", {})
    assert "alpha" in out
    assert "beta-local" in out


def test_list_workplaces_kind_filter(tmp_path: Path) -> None:
    store.create_workplace({"id": "tun_x", "name": "X", "kind": "tunnel"})
    store.update_agent("ops", {"workplace_scope": "all_tunnels"})
    sandbox.bind_agent("ops")
    out = execute("list_workplaces", {"kind": "ssh"})
    assert "0" in out or "none" in out.lower()
