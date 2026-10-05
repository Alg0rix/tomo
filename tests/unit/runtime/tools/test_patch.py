"""patch tool (sandbox) tests."""

from __future__ import annotations

import pytest

from app.runtime.tools import sandbox
from app.runtime.tools.registry import execute, reset_registry
from app.services import store
from tests.fakes.access import owned_admin_scope


@pytest.fixture(autouse=True)
def _reset(tmp_path) -> None:
    reset_registry()
    store.rebind(tmp_path / "patch.db")
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


def test_patch_happy_path(admin_fs) -> None:
    work = admin_fs
    (work / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    result = execute(
        "patch",
        {
            "path": "a.txt",
            "patch": "@@ -1,3 +1,3 @@\n one\n-two\n+TWO\n three\n",
        },
    )
    assert "Applied" in result
    assert (work / "a.txt").read_text(encoding="utf-8") == "one\nTWO\nthree\n"


def test_patch_create_file(admin_fs) -> None:
    work = admin_fs
    target = work / "new.txt"
    if target.exists():
        target.unlink()
    result = execute(
        "patch",
        {
            "path": "new.txt",
            "patch": "@@ -0,0 +1,2 @@\n+hello\n+world\n",
        },
    )
    assert "Applied" in result
    assert target.read_text(encoding="utf-8") == "hello\nworld\n"


def test_patch_missing_file(admin_fs) -> None:
    result = execute(
        "patch",
        {
            "path": "nope.txt",
            "patch": "@@ -1 +1 @@\n-x\n+y\n",
        },
    )
    assert result.startswith("Error")
    assert "not found" in result.lower()


def test_patch_escape_path(admin_fs) -> None:
    # Approved unrestricted exception (see test_write_rejects_dotdot_escape):
    # an explicitly unrestricted Admin host patch follows the OS account.
    # Escape rejection for untrusted Members is enforced by restricted
    # containers, covered by tests/integration/test_multi_user_isolation.py.
    work = admin_fs
    result = execute(
        "patch",
        {
            "path": "../x",
            "patch": "@@ -0,0 +1,1 @@\n+x\n",
        },
    )
    assert result.startswith("Applied")
    assert not (work / "x").exists()


def test_patch_bad_hunks(admin_fs) -> None:
    work = admin_fs
    (work / "a.txt").write_text("x\n", encoding="utf-8")
    result = execute("patch", {"path": "a.txt", "patch": "not a patch"})
    assert result.startswith("Error")
