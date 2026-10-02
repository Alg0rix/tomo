"""Chat pagination keeps turns intact and the complete query rail lightweight."""
from fastapi.testclient import TestClient

from app.core.deps import require_auth
from app.main import app
from app.services import store


def test_history_pages_and_full_rail(tmp_path):
    store.rebind(tmp_path / "pages.db")
    sid = store.create_swarm_session(["main"], user_id="web")
    other = store.create_swarm_session(["main"], user_id="someone-else")
    store.append_session_history(sid, {"type": "compact", "content": "preamble"})
    for n in range(45):
        store.append_session_history(sid, {"type": "user", "content": f"Question {n} " + "x" * 500})
        store.append_session_history(other, {"type": "user", "content": "private"})
        store.append_session_history(sid, {"type": "tool_call", "function": "bash", "call_id": f"call-{n}"})
        store.append_session_history(sid, {"type": "tool_output", "content": "tool data", "call_id": f"call-{n}"})
        store.append_session_history(sid, {"type": "final", "content": f"Answer {n}"})

    app.dependency_overrides[require_auth] = lambda: None
    try:
        client = TestClient(app)
        url = f"/api/sessions/{sid}/chat"
        page = client.get(url, params={"limit": 20}).json()
        assert len(page["entries"]) == 80
        assert page["entries"][0]["content"].startswith("Question 25 ")
        assert page["entries"][-1]["content"] == "Answer 44"
        assert page["entries"][1]["call_id"] == "call-25"
        assert page["has_more"] is True
        newer = page["entries"]
        older = client.get(url, params={"limit": 20, "before": page["before"]}).json()
        assert len(older["entries"]) == 80
        oldest = client.get(url, params={"limit": 20, "before": older["before"]}).json()
        assert oldest["has_more"] is False
        assert oldest["entries"][0]["type"] == "compact"
        complete = client.get(url).json()["entries"]
        assert oldest["entries"] + older["entries"] + newer == complete

        store.append_session_history(sid, {"type": "user", "content": "New turn"})
        refresh = client.get(url, params={"since": page["before"]}).json()
        assert refresh["entries"][:-1] == newer
        assert refresh["entries"][-1]["content"] == "New turn"
        rail = client.get(url + "/queries").json()["queries"]
        assert len(rail) == 46
        assert all(set(row) == {"message_id", "content"} for row in rail)
        assert max(len(row["content"]) for row in rail) <= 300
        assert client.get(f"/api/sessions/{other}/chat/queries").status_code in (403, 404)
        for params in ({"limit": 0}, {"limit": 51}, {"before": -1}, {"before": 1, "since": 1}):
            assert client.get(url, params=params).status_code == 422
        from app.models.mixins.messages import clear_session_history

        store.with_db(lambda conn: clear_session_history(conn, sid))
        empty = client.get(url, params={"limit": 20}).json()
        assert empty["entries"] == []
        assert empty["has_more"] is False
        assert empty["before"] is None
    finally:
        app.dependency_overrides.pop(require_auth, None)
