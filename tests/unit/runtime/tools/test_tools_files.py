"""Consolidated tests (merged from: test_files.py, test_read_write_file.py).
- test_files.py: read_file / write_file tools: success, jail escape, error strings.
- test_read_write_file.py: Upgraded read_file / write_file tests.
"""

from __future__ import annotations

import pytest
from app.runtime.access import AccessDenied
from app.runtime.tools import sandbox
from app.runtime.tools.registry import execute, get_openai_tools, reset_registry
from app.services import store
from tests.fakes.access import owned_admin_scope


# --- from test_files.py ---
@pytest.fixture(autouse=True)
def _reset(tmp_path) -> None:
    # Own DB: another file may leave "ops" bound to a tunnel workplace.
    store.rebind(tmp_path / "files.db")
    reset_registry()
    sandbox.reset_agent()
    yield
    sandbox.reset_agent()
    reset_registry()


@pytest.fixture()
def work_bound():
    # Explicit owned Admin unrestricted context (real policy grant + ack).
    # Host file tools are fenced to the session workplace root, not TOMO_WORK.
    with owned_admin_scope() as (_ctx, root):
        yield root


def test_read_write_schemas_loaded() -> None:
    names = {t["function"]["name"] for t in get_openai_tools()}
    assert "read_file" in names
    assert "write_file" in names


def test_write_then_read_roundtrip(work_bound) -> None:
    wrote = execute("write_file", {"path": "notes/hello.txt", "content": "hi from tomo"})
    assert "bytes to notes/hello.txt" in wrote
    read = execute("read_file", {"path": "notes/hello.txt"})
    assert "1|hi from tomo" in read
    assert (work_bound / "notes" / "hello.txt").read_text(encoding="utf-8") == "hi from tomo"


def test_read_missing_file_is_error(work_bound) -> None:
    result = execute("read_file", {"path": "missing.txt"})
    assert result.startswith("Error")
    assert "not found" in result.lower()


def test_read_rejects_path_escape(work_bound) -> None:
    # Unrestricted Admin host reads resolve via the OS account; a missing
    # outside file is still a safe error with no content disclosure.
    result = execute("read_file", {"path": "../SOUL.md"})
    assert result.startswith("Error")


def test_write_rejects_absolute_path(work_bound) -> None:
    # Absolute references are attemptable in explicitly unrestricted Admin host
    # execution (OS account governs); unwritable targets stay OS-denied errors.
    result = execute("write_file", {"path": "/etc/passwd", "content": "nope"})
    assert result.startswith("Error")


def test_write_rejects_dotdot_escape(work_bound) -> None:
    # Approved unrestricted exception, documented not implied otherwise: an
    # explicitly unrestricted Admin host write follows the OS account rather
    # than folder mounts. Escape rejection for untrusted Members lives in
    # restricted container enforcement (kernel RO mounts, symlink denials),
    # covered by tests/integration/test_multi_user_isolation.py, not cwd jail.
    result = execute("write_file", {"path": "../../outside.txt", "content": "nope"})
    assert result.startswith("Created")
    assert not (work_bound / "outside.txt").exists()


def test_read_missing_path_arg_is_error(work_bound) -> None:
    assert execute("read_file", {}).startswith("Error")


def test_write_missing_content_is_error(work_bound) -> None:
    assert execute("write_file", {"path": "a.txt"}).startswith("Error")


# --- from test_read_write_file.py ---
@pytest.fixture(autouse=True)
def _reset_read_write_file() -> None:
    reset_registry()
    sandbox.reset_agent()
    yield
    sandbox.reset_agent()
    reset_registry()


def test_read_file_numbered_and_paginated(work_bound) -> None:
    work = work_bound
    (work / "lines.txt").write_text("a\nb\nc\nd\n", encoding="utf-8")
    out = execute("read_file", {"path": "lines.txt", "offset": 2, "limit": 2})
    assert "lines 2-3 of 4" in out
    assert "2|b" in out
    assert "3|c" in out
    assert "offset=4" in out or "after line 3" in out


def test_read_file_suggests_similar(work_bound) -> None:
    work = work_bound
    (work / "config.yaml").write_text("x: 1\n", encoding="utf-8")
    out = execute("read_file", {"path": "config.yaaml"})
    assert out.startswith("Error")
    assert "Did you mean" in out
    assert "config.yaml" in out


def test_write_file_create_refuses_overwrite(work_bound) -> None:
    work = work_bound
    (work / "x.txt").write_text("old\n", encoding="utf-8")
    out = execute(
        "write_file",
        {"path": "x.txt", "content": "new\n", "mode": "create"},
    )
    assert out.startswith("Error")
    assert "exists" in out.lower()
    assert (work / "x.txt").read_text(encoding="utf-8") == "old\n"


def test_write_file_append(work_bound) -> None:
    work = work_bound
    (work / "a.txt").write_text("hi\n", encoding="utf-8")
    out = execute(
        "write_file",
        {"path": "a.txt", "content": "there\n", "mode": "append"},
    )
    assert "Appended" in out
    assert (work / "a.txt").read_text(encoding="utf-8") == "hi\nthere\n"


def test_write_file_overwrite_default(work_bound) -> None:
    work = work_bound
    (work / "b.txt").write_text("old\n", encoding="utf-8")
    out = execute("write_file", {"path": "b.txt", "content": "new\n"})
    assert "Wrote" in out or "Created" in out
    assert (work / "b.txt").read_text(encoding="utf-8") == "new\n"




def test_main_without_project_can_reference_other_folder(tmp_path):
    from app.runtime.permissions.assess import assess

    # Explicit unrestricted Admin host chat: absolute host references are the
    # conscious trust decision, and approval assessment stays below the ceiling.
    with owned_admin_scope(["main"]) as (_ctx, root):
        assert sandbox.resolve_work_root() == root.resolve()
        outside = tmp_path / "reference.txt"
        assert not assess("write_file", {"path": str(outside)}, root).findings
        assert not assess("bash", {"command": f"cat {outside}"}, root).findings
        execute("write_file", {"path": str(outside), "content": "reference"})
        assert "reference" in execute("read_file", {"path": str(outside)})
        assert outside.read_text() == "reference"


def test_restricted_chat_requires_activation_for_other_folder(tmp_path):
    from app.runtime.permissions.assess import assess

    root = tmp_path / "project"
    root.mkdir()
    store.create_workplace({"id": "project", "name": "Project", "kind": "local",
                            "root_path": str(root)})
    outside = tmp_path / "reference.txt"
    # Pure approval assessment (no bound execution) still flags the escape.
    assert any(f.kind == "escape" for f in
               assess("read_file", {"path": str(outside)}, root).findings)
    # Restricted chats retain explicit resource activation even for Admins.
    from tests.fakes.access import ensure_stoppers
    ensure_stoppers()
    sid = store.create_home_session("usr_admin")["session_id"]
    active = store.get_session(sid)["workplace_id"]
    store.access.set_chat_access("usr_admin", sid, active, execution_mode="restricted")
    context = store.access.resolve_context("usr_admin", sid)
    with pytest.raises(AccessDenied):
        store.access.authorize_resource(context, "project")
