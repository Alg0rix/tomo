"""Remote jobs use their recorded workplace and never retry uncertain starts."""
from unittest.mock import patch

import pytest

from app.services import background_job_backends as backends


def test_legacy_contract_does_not_execute() -> None:
    with patch.object(backends, "_rpc", return_value={"status": "exited", "returncode": 0}) as rpc:
        with pytest.raises(RuntimeError, match="unsupported"):
            backends.start_remote("tunnel", "old", "touch dangerous", "/work")
    assert rpc.call_count == 1
    assert rpc.call_args.args[2] == "process_status"


def test_start_timeout_retains_correlation_handle() -> None:
    with patch.object(backends, "_rpc", side_effect=[{"process_contract": 1}, TimeoutError("timeout")]) as rpc:
        with pytest.raises(backends.RemoteStartUnknown) as caught:
            backends.start_remote("tunnel", "original", "echo hello", "/work")
    assert caught.value.backend_handle == rpc.call_args.args[3]["id"]
    assert rpc.call_count == 2


def test_missing_handle_and_disconnection_never_become_success() -> None:
    for failure in (ConnectionError("offline"), RuntimeError("unknown job id")):
        with patch.object(backends, "_rpc", side_effect=failure):
            got = backends.observe_remote("tunnel", "original", "job_handle")
        assert got["status"] == "unknown"
        assert got["exit_code"] is None


def test_nonzero_exit_and_stop_use_original_workplace() -> None:
    result = {"process_contract": 1, "status": "exited", "returncode": 9}
    with patch.object(backends, "_rpc", return_value=result) as rpc:
        got = backends.stop_remote("ssh", "old-workplace", "old-handle")
    assert got["status"] == "failed"
    assert got["exit_code"] == 9
    rpc.assert_called_once_with("ssh", "old-workplace", "process_kill", {"id": "old-handle"})


def test_confirmed_rejection_has_no_unknown_handle() -> None:
    with patch.object(backends, "_rpc", side_effect=[{"process_contract": 1}, backends.RemoteStartRejected("busy")]):
        with pytest.raises(backends.RemoteStartRejected):
            backends.start_remote("tunnel", "original", "echo hi", "/work")


def test_explicit_unknown_workplace_fails_closed() -> None:
    with patch("app.services.store.store.get_agent", return_value={"id": "a"}), patch(
        "app.runtime.tools.workplace_remote._agent_allowed_workplaces", return_value=[]
    ):
        with pytest.raises(ValueError, match="not allowed"):
            backends.resolve_backend("offline-host")
