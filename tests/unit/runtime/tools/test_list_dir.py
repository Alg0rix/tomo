"""list_dir + jail_path absolute-under-root tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.runtime.tools import sandbox
from app.runtime.tools.registry import execute, reset_registry
from app.runtime.tools.sandbox import jail_path
from app.services import store
from tests.fakes.access import owned_admin_scope


@pytest.fixture(autouse=True)
def _reset(tmp_path) -> None:
    store.rebind(tmp_path / "list_dir.db")
    reset_registry()
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


def test_jail_path_absolute_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "wp"
    root.mkdir()
    outside = tmp_path / "other" / "x.txt"
    outside.parent.mkdir()
    outside.write_text("y", encoding="utf-8")
    got = jail_path(root, str(outside.resolve()))
    assert isinstance(got, str) and got.startswith("Error")


def test_list_dir_lists_files(admin_fs) -> None:
    work = admin_fs
    (work / "note.txt").write_text("hi", encoding="utf-8")
    (work / "sub").mkdir(exist_ok=True)
    (work / "sub" / "nested.txt").write_text("n", encoding="utf-8")
    out = execute("list_dir", {"path": ".", "recursive": True, "max_depth": 2})
    assert "Workplace root:" in out
    assert "note.txt" in out
    assert "sub" in out
