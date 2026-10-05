"""process + bash background job tests."""

from __future__ import annotations

import time

import pytest

from app.runtime.tools import sandbox
from app.runtime.tools.registry import execute, reset_registry
from app.services import store
from app.services.background_jobs import manager as _job_manager
from tests.fakes.access import owned_admin_scope


@pytest.fixture(autouse=True)
def _reset(tmp_path) -> None:
    reset_registry()
    _job_manager.reset()
    store.rebind(tmp_path / "process.db")
    sandbox.reset_agent()
    yield
    _job_manager.reset()
    sandbox.reset_agent()
    reset_registry()


@pytest.fixture()
def admin_fs():
    # Explicit owned Admin unrestricted context (real policy grant + ack).
    with owned_admin_scope() as (_ctx, root):
        yield root


def test_bash_background_registers_job(admin_fs) -> None:
    result = execute(
        "bash", {"command": "sleep 0.3; echo done", "background": True}
    )
    assert result.startswith("Started background job")
    job_id = result.splitlines()[0].rsplit(" ", 1)[-1]
    listed = execute("process", {"action": "list"})
    assert job_id in listed
    # wait for completion
    deadline = time.time() + 2
    status = ""
    while time.time() < deadline:
        status = execute("process", {"action": "status", "id": job_id})
        if "succeeded" in status:
            break
        time.sleep(0.05)
    assert "succeeded" in status


def test_process_kill(admin_fs) -> None:
    result = execute("bash", {"command": "sleep 30", "background": True})
    job_id = result.splitlines()[0].rsplit(" ", 1)[-1]
    killed = execute("process", {"action": "kill", "id": job_id})
    assert job_id in killed
    assert "stopped" in killed or "returncode" in killed


def test_process_unknown_id_is_error(admin_fs) -> None:
    assert execute("process", {"action": "status", "id": "job_nope"}).startswith(
        "Error"
    )


def test_process_bad_action_is_error(admin_fs) -> None:
    assert execute("process", {"action": "pause"}).startswith("Error")
