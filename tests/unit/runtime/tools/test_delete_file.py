"""delete_file tool tests."""

from __future__ import annotations

import pytest

from app.runtime.tools import sandbox
from app.runtime.tools.registry import execute, reset_registry
from app.services import store
from tests.fakes.access import owned_admin_scope


@pytest.fixture(autouse=True)
def _reset(tmp_path) -> None:
    store.rebind(tmp_path / "delete_file.db")
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


def test_delete_file_happy_path(admin_fs) -> None:
    work = admin_fs
    target = work / "gone.txt"
    target.write_text("bye", encoding="utf-8")
    result = execute("delete_file", {"path": "gone.txt"})
    assert "Deleted" in result
    assert not target.exists()


def test_delete_file_missing_is_error(admin_fs) -> None:
    result = execute("delete_file", {"path": "nope.txt"})
    assert result.startswith("Error")


def test_delete_file_rejects_directory(admin_fs) -> None:
    work = admin_fs
    (work / "subdir").mkdir(parents=True, exist_ok=True)
    result = execute("delete_file", {"path": "subdir"})
    assert result.startswith("Error")
    assert "directory" in result.lower()
