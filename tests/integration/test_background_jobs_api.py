"""Job snapshots and controls must remain private even to shared-chat admins."""

from fastapi.testclient import TestClient
import asyncio
import time

import pytest

from app.main import app
from app.services import store


def login(client, username):
    response = client.post(
        "/login",
        data={"username": username, "password": "password1"},
        follow_redirects=False,
    )
    assert response.status_code == 303


@pytest.mark.parametrize('kind', ['session', 'agent'])
async def test_deletion_keeps_other_async_requests_responsive(monkeypatch, kind):
    from app.api import rest
    from app.services.terminals import terminal_manager

    def slow_delete(_id):
        time.sleep(.1)
        return True
    async def close(_sid):
        pass
    monkeypatch.setattr(rest, 'require_owned_session', lambda *args: None)
    monkeypatch.setattr(terminal_manager, 'close_session', close)
    if kind == 'session':
        monkeypatch.setattr(rest.store, 'delete_session', slow_delete)
        task = asyncio.create_task(rest.delete_session_api('sid', None, None))
    else:
        monkeypatch.setattr(rest.store, 'delete_agent', slow_delete)
        task = asyncio.create_task(rest.delete_agent('agent', None))
    await asyncio.sleep(.02)
    assert not task.done(), 'Deletion blocked unrelated event-loop work'
    assert await task == {'success': True}


def test_private_process_api_and_control_isolation(tmp_path, monkeypatch):
    from app.api import processes

    store.rebind(tmp_path / "jobs-api.db")
    alice = store.create_user({"username": "jobs_alice", "password": "password1"})
    store.create_user(
        {"username": "jobs_bob", "password": "password1", "role": "admin"}
    )
    sid = store.create_swarm_session(["main"], user_id=alice["id"])
    sibling = store.create_swarm_session(["main"], user_id=alice["id"])
    # Create records inside lifespan so startup's honest restart recovery does
    # not transform synthetic active jobs before the API assertions.
    with TestClient(app) as client:
        first = store.create_background_job(
            {
                "session_id": sid,
                "user_id": alice["id"],
                "command": "sleep 60",
                "status": "running",
                "backend_handle": "PRIVATE_HANDLE",
                "delivery": {"token": "PRIVATE_TOKEN"},
                "card_message_id": 9876,
                "stdout": "<script>output</script>",
                "stderr": "error",
            }
        )
        second = store.create_background_job(
            {
                "session_id": sid,
                "user_id": alice["id"],
                "command": "sleep 70",
                "status": "running",
            }
        )
        base = f"/api/sessions/{sid}/processes"
        assert client.get(base).status_code == 401
        login(client, "jobs_alice")
        listing = client.get(base).json()["jobs"]
        assert {job["id"] for job in listing} == {first["id"], second["id"]}
        assert all("stdout" not in job for job in listing)
        detail = client.get(base + "/" + first["id"])
        assert detail.status_code == 200
        assert "PRIVATE_" not in detail.text
        assert "backend_handle" not in detail.json()
        assert (
            "delivery" not in detail.json() and "card_message_id" not in detail.json()
        )
        logs = client.get(base + "/" + first["id"] + "/logs").json()
        assert logs["stdout"] == "<script>output</script>"
        assert logs["stderr"] == "error"
        assert client.get(base + "/" + first["id"] + "/logs?tail=0").status_code == 422
        assert (
            client.get(base + "/" + first["id"] + "/logs?tail=1048577").status_code
            == 422
        )
        for suffix in ("", "/logs", "/stop", "/close-monitoring"):
            url = f"/api/sessions/{sibling}/processes/{first['id']}" + suffix
            method = (
                client.post if suffix in ("/stop", "/close-monitoring") else client.get
            )
            assert method(url).status_code == 404
        calls = []

        def stop(session_id, job_id):
            calls.append((session_id, job_id))
            return store.update_background_job(job_id, {"status": "stopping"})

        monkeypatch.setattr(processes.manager, "stop_job", stop)
        for origin in ("https://evil.example", "http://["):
            assert (
                client.post(
                    base + "/" + first["id"] + "/stop", headers={"origin": origin}
                ).status_code
                == 403
            )
        assert calls == []
        stopped = client.post(
            base + "/" + first["id"] + "/stop", headers={"origin": "http://testserver"}
        )
        assert stopped.status_code == 200 and stopped.json()["status"] == "stopping"
        assert calls == [(sid, first["id"])]
        assert store.get_background_job(second["id"])["status"] == "running"
        assert (
            client.post(base + "/" + first["id"] + "/close-monitoring").status_code
            == 409
        )
        store.update_background_job(first["id"], {"status": "unknown"})
        assert (
            client.post(
                base + "/" + first["id"] + "/close-monitoring",
                headers={"origin": "https://evil.example"},
            ).status_code
            == 403
        )
        closed = client.post(base + "/" + first["id"] + "/close-monitoring").json()
        assert closed["status"] == "unknown" and closed["monitoring_closed"]
        assert len(calls) == 1  # Closing monitoring never invokes backend stop.
        login(client, "jobs_bob")
        for suffix in ("", "/" + first["id"], "/" + first["id"] + "/logs"):
            assert client.get(base + suffix).status_code == 404
        assert client.post(base + "/" + first["id"] + "/stop").status_code == 404
        assert (
            client.post(base + "/" + first["id"] + "/close-monitoring").status_code
            == 404
        )
