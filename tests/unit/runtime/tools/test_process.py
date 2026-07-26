"""process + bash background job tests."""

from __future__ import annotations

import time

import pytest

from app.core import home
from app.runtime.tools import process_registry, sandbox
from app.runtime.tools.registry import execute, reset_registry


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_registry()
    process_registry.reset()
    sandbox.reset_agent()
    yield
    process_registry.reset()
    sandbox.reset_agent()
    reset_registry()


def test_bash_background_registers_job() -> None:
    work = home.agent_work_dir("ops")
    work.mkdir(parents=True, exist_ok=True)
    sandbox.bind_agent("ops")
    result = execute(
        "bash", {"command": "sleep 0.3; echo done", "background": True}
    )
    assert result.startswith("Started background job")
    job_id = result.rsplit(" ", 1)[-1]
    listed = execute("process", {"action": "list"})
    assert job_id in listed
    # wait for completion
    deadline = time.time() + 2
    status = ""
    while time.time() < deadline:
        status = execute("process", {"action": "status", "id": job_id})
        if "exited" in status:
            break
        time.sleep(0.05)
    assert "exited" in status


def test_process_kill() -> None:
    work = home.agent_work_dir("ops")
    work.mkdir(parents=True, exist_ok=True)
    sandbox.bind_agent("ops")
    result = execute("bash", {"command": "sleep 30", "background": True})
    job_id = result.rsplit(" ", 1)[-1]
    killed = execute("process", {"action": "kill", "id": job_id})
    assert job_id in killed
    assert "exited" in killed or "returncode" in killed


def test_process_unknown_id_is_error() -> None:
    assert execute("process", {"action": "status", "id": "job_nope"}).startswith(
        "Error"
    )


def test_process_bad_action_is_error() -> None:
    assert execute("process", {"action": "pause"}).startswith("Error")
