"""Sandbox path jail helpers + workplace-backed cwd (Slice D)."""

from __future__ import annotations

from pathlib import Path

from app.core import home
from app.runtime.tools.sandbox import jail_path, resolve_work_root, bind_agent, reset_agent
from app.runtime.tools.workplace_ctx import reset_workplace
from app.services import store


def test_resolve_work_root_creates_dir(tmp_path: Path) -> None:
    store.rebind(tmp_path / "sandbox-default.db")
    reset_agent()
    reset_workplace()
    bind_agent("ops")
    try:
        root = resolve_work_root()
        assert root.is_dir()
        assert root.name == "ops"  # $TOMO_WORK/<agent>
        assert "ops" in str(root)
    finally:
        reset_agent()
        reset_workplace()


def test_resolve_work_root_uses_local_workplace(tmp_path: Path) -> None:
    store.rebind(tmp_path / "sandbox-wp.db")
    wp_root = tmp_path / "agent-cwd"
    wp_root.mkdir()
    store.create_workplace(
        {"id": "wp_cwd", "name": "CWD", "kind": "local", "root_path": str(wp_root)}
    )
    store.update_agent("ops", {"workplace_id": "wp_cwd"})
    reset_agent()
    reset_workplace()
    bind_agent("ops")
    try:
        root = resolve_work_root()
        assert root == wp_root.resolve()
        assert root != home.agent_work_dir("ops").resolve()
    finally:
        reset_agent()
        reset_workplace()


def test_resolve_work_root_falls_back_without_workplace(tmp_path: Path) -> None:
    store.rebind(tmp_path / "sandbox-fallback.db")
    reset_agent()
    reset_workplace()
    bind_agent("ops")
    try:
        root = resolve_work_root()
        assert root == home.agent_work_dir("ops").resolve()
    finally:
        reset_agent()
        reset_workplace()


def test_force_work_dir_ignores_agent_local_workplace(tmp_path: Path) -> None:
    """Chat 'Tomo work dir' must not use agent permanent local WP (/tmp, etc.)."""
    from app.runtime.tools.workplace_ctx import bind_workplace

    store.rebind(tmp_path / "sandbox-force.db")
    wp_root = tmp_path / "tmp-like"
    wp_root.mkdir()
    store.create_workplace(
        {"id": "wp_tmp", "name": "tmp-work", "kind": "local", "root_path": str(wp_root)}
    )
    store.update_agent("ops", {"workplace_id": "wp_tmp"})
    reset_agent()
    reset_workplace()
    bind_agent("ops")
    toks = bind_workplace(force_work_dir=True)
    try:
        root = resolve_work_root()
        assert root == home.agent_work_dir("ops").resolve()
        assert root != wp_root.resolve()
    finally:
        reset_workplace(toks)
        reset_agent()


def test_jail_allows_relative(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    target = jail_path(root, "a/b.txt")
    assert isinstance(target, Path)
    assert target == (root / "a" / "b.txt").resolve()


def test_jail_rejects_absolute(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    err = jail_path(root, "/etc/passwd")
    assert isinstance(err, str) and err.startswith("Error")


def test_jail_rejects_escape(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    err = jail_path(root, "../secret")
    assert isinstance(err, str) and err.startswith("Error")
