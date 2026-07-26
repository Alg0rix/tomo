"""bash tool: sandbox cwd, timeout, and error-string contract."""

from __future__ import annotations

import pytest

from app.core import home
from app.runtime.tools import bash, sandbox
from app.runtime.tools.registry import execute, get_openai_tools, reset_registry


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_registry()
    sandbox.reset_agent()
    yield
    sandbox.reset_agent()
    reset_registry()


def test_bash_schema_loaded() -> None:
    schema = next(t for t in get_openai_tools() if t["function"]["name"] == "bash")
    assert "command" in schema["function"]["parameters"]["properties"]


def test_bash_echo_in_work_dir() -> None:
    work = home.agent_work_dir("ops")
    work.mkdir(parents=True, exist_ok=True)
    sandbox.bind_agent("ops")
    result = execute("bash", {"command": "pwd && echo hello-tomo"})
    assert "hello-tomo" in result
    assert str(work.resolve()) in result


def test_bash_missing_command_is_error() -> None:
    assert execute("bash", {}).startswith("Error")


def test_bash_timeout_is_error_string() -> None:
    sandbox.bind_agent("ops")
    result = bash.run({"command": "sleep 5", "timeout": 0.2})
    assert result.startswith("Error")
    assert "timed out" in result.lower()


def test_bash_nonzero_exit_includes_code() -> None:
    sandbox.bind_agent("ops")
    result = execute("bash", {"command": "exit 7"})
    assert "7" in result


def test_bash_run_never_raises_on_bad_args() -> None:
    try:
        result = bash.run("not a dict")  # type: ignore[arg-type]
    except Exception as exc:  # pragma: no cover
        raise AssertionError(f"run raised: {exc!r}") from exc
    assert result.startswith("Error")
