"""Exercise actual file rotation and the public log CLI, without mocked I/O."""
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.core.logging import JsonFormatter, PrivateRotatingFileHandler


@pytest.mark.asyncio
async def test_http_to_tool_logs_correlated_failures_without_payload(tmp_path):
    import httpx
    from fastapi import FastAPI

    from app.core.observability import RequestLoggingMiddleware, log_context
    from app.runtime.tools.registry import execute_async

    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.post("/probe")
    async def probe():
        return {"result": await execute_async("nonexistent_log_probe", {"secret": "DO_NOT_LOG"})}

    path = tmp_path / "trace.jsonl"
    handler = PrivateRotatingFileHandler(path, maxBytes=100000, backupCount=1)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("app.core.observability")
    previous = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/probe?secret=DO_NOT_LOG")
        assert response.status_code == 200
        request_id = response.headers["x-request-id"]
        records = [json.loads(line) for line in path.read_text().splitlines()]
        assert {r["request_id"] for r in records} == {request_id}
        assert any(r["type"] == "tool" and r["event"] == "failed" and r["tool_name"] == "nonexistent_log_probe" for r in records)
        assert any(r["type"] == "http" and r.get("status_code") == 200 for r in records)
        assert "DO_NOT_LOG" not in path.read_text()
        assert log_context.get() == {}
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
        handler.close()


def test_rotated_logs_remain_private_and_cli_filters(tmp_path):
    path = tmp_path / "tomo.jsonl"
    handler = PrivateRotatingFileHandler(path, maxBytes=400, backupCount=2, encoding="utf-8")
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("log-test")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        for i in range(6):
            logger.info("event %s", i, extra={"log_type": "session_title", "session_id": "s1", "event": "generated"})
        logger.error("failure", extra={"log_type": "session_title", "session_id": "s2"})
    finally:
        logger.removeHandler(handler)
        handler.close()
    files = list(tmp_path.iterdir())
    assert len(files) == 3
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in files)
    env = {**os.environ, "TOMO_LOG_DIR": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "-m", "cli", "logs", "--type", "session_title", "--session", "s2", "--level", "ERROR", "--json"],
        env=env, text=True, capture_output=True, check=True,
    )
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(records) == 1
    assert records[0]["message"] == "failure"


@pytest.mark.skipif(sys.platform != "linux", reason="Uses /proc to synchronize CLI startup")
def test_live_cli_follows_rotation(tmp_path):
    path = tmp_path / "tomo.jsonl"
    path.touch()
    output = tmp_path / "output"
    record = {"timestamp": "now", "level": "INFO", "type": "session_title", "message": "after rotation"}
    with output.open("w") as stdout:
        process = subprocess.Popen(
            [sys.executable, "-m", "cli", "logs", "-f", "-n", "0", "--json"],
            env={**os.environ, "TOMO_LOG_DIR": str(tmp_path)}, stdout=stdout, stderr=subprocess.PIPE,
        )
        try:
            # Wait until the CLI has opened the actual log, not an arbitrary startup sleep.
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                # Fds can close between glob and realpath under load; retry
                # the snapshot instead of failing on a stale entry.
                try:
                    fds = list(Path(f"/proc/{process.pid}/fd").glob("*"))
                    opened = any(os.path.realpath(fd) == str(path) for fd in fds)
                except FileNotFoundError:
                    opened = False
                if opened:
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("CLI did not open log file")
            path.rename(tmp_path / "tomo.jsonl.1")
            path.write_text(json.dumps(record) + "\n")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not output.read_text():
                time.sleep(0.05)
            assert json.loads(output.read_text()) == record
        finally:
            process.terminate()
            process.wait(timeout=5)
