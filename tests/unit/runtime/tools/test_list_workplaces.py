"""list_workplaces tool — registry catalog, not filesystem."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.runtime.tools import sandbox
from app.runtime.tools.registry import execute, reset_registry
from app.services import store
from tests.fakes.access import owned_admin_scope


@pytest.fixture(autouse=True)
def _db(tmp_path: Path) -> None:
    reset_registry()
    store.rebind(tmp_path / "list_wp.db")
    sandbox.reset_agent()
    yield
    sandbox.reset_agent()
    reset_registry()


@pytest.fixture()
def admin_fs():
    # Explicit owned Admin unrestricted context (real policy grant + ack).
    # Host tools are fenced to the session workplace root, not TOMO_WORK.
    with owned_admin_scope() as (_ctx, root):
        yield root


def test_list_workplaces_ops_all_tunnels(tmp_path: Path, admin_fs) -> None:
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
    out = execute("list_workplaces", {})
    assert "SECOND-DEV" in out
    assert "tun_second" in out
    # Current contract: authorized but un-enabled resources are listed with
    # their enablement status instead of being hidden; activation requires
    # the user, never tools or approvals.
    assert "local-only" in out
    assert "not enabled in chat" in out
    assert "tools and approvals cannot activate additional resources" in out


def test_list_workplaces_coordinator_sees_all(tmp_path: Path, admin_fs) -> None:
    store.create_workplace({"id": "tun_a", "name": "alpha", "kind": "tunnel"})
    store.create_workplace(
        {
            "id": "wp_b",
            "name": "beta-local",
            "kind": "local",
            "root_path": str(tmp_path),
        }
    )
    out = execute("list_workplaces", {})
    assert "alpha" in out
    assert "beta-local" in out


def test_list_workplaces_kind_filter(tmp_path: Path, admin_fs) -> None:
    store.create_workplace({"id": "tun_x", "name": "X", "kind": "tunnel"})
    store.update_agent("ops", {"workplace_scope": "all_tunnels"})
    out = execute("list_workplaces", {"kind": "ssh"})
    assert "0" in out or "none" in out.lower()
