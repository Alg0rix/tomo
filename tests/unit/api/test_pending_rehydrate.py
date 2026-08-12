"""Session pending API includes todos + HITL for refresh rehydrate."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.deps import require_auth
from app.main import app
from app.runtime.permissions import hitl
from app.runtime.tools import todo as todo_mod
from app.services import store


def test_pending_includes_todos_and_approvals(tmp_path) -> None:
    store.rebind(tmp_path / "pending_rehydrate.db")
    hitl.clear_all_pending()
    sid = store.create_swarm_session(["main"], user_id="web")

    todo_mod.get_store(sid).write(
        [
            {"id": "1", "content": "First", "status": "pending"},
            {"id": "2", "content": "Second", "status": "in_progress"},
        ],
        merge=False,
    )
    payload = hitl.create_approval(
        tool="bash",
        args={"command": "ls"},
        findings=[],
        description="list files",
        session_id=sid,
    )

    app.dependency_overrides[require_auth] = lambda: None
    client = TestClient(app)
    try:
        res = client.get(f"/api/sessions/{sid}/pending")
        assert res.status_code == 200
        data = res.json()
        assert data["session_id"] == sid
        assert data["active_turn"] is False
        assert len(data["approvals"]) == 1
        assert data["approvals"][0]["id"] == payload["id"]
        assert data["approvals"][0]["tool"] == "bash"
        assert len(data["todos"]) == 2
        assert data["todos"][0]["content"] == "First"
        assert data["todos"][1]["status"] == "in_progress"
    finally:
        app.dependency_overrides.pop(require_auth, None)
        hitl.clear_all_pending()


def test_resume_chrome_sse_emits_hitl_and_todos() -> None:
    from app.api.stream import _resume_chrome_sse

    hitl.clear_all_pending()
    sid = "ses_chrome_test"
    todo_mod.get_store(sid).write(
        [{"id": "a", "content": "Plan", "status": "pending"}],
        merge=False,
    )
    hitl.create_clarify(question="Which path?", choices=["A", "B"], session_id=sid)

    chunks = _resume_chrome_sse(sid, agent_id="main")
    raw = "".join(chunks)
    assert "event: todos" in raw
    assert "Plan" in raw
    assert "event: clarify_required" in raw
    assert "Which path?" in raw
    hitl.clear_all_pending()
