"""Token Monitor usage / modules catalog tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.deps import require_auth
from app.main import app
from app.services import store
from modules.token_monitor import ledger


def test_record_and_dashboard(tmp_path) -> None:
    store.rebind(tmp_path / "usage.db")
    assert store.is_module_enabled("token_monitor") is True

    admin = store.get_user_by_username("admin")
    sid = store.get_or_create_session("main", admin["id"])
    store.with_db(
        lambda conn: ledger.record_event(
            conn,
            session_id=sid,
            agent_id="main",
            turns=1,
            prompt_tokens=40,
            completion_tokens=60,
            message_preview="hello usage",
        )
    )
    store.dispatch_turn_end(
        session_id=sid,
        agent_id="ops",
        message="ops turn",
        prompt_tokens=10,
        completion_tokens=5,
    )

    dash = store.with_db(ledger.dashboard)
    assert dash["summary"]["today"]["turns"] >= 2
    # Cumulative in+out across both events: (40+60) + (10+5) = 115
    assert dash["summary"]["today"]["tokens"] >= 115
    assert dash["summary"]["today"]["prompt_tokens"] >= 50
    assert dash["summary"]["today"]["completion_tokens"] >= 65
    assert any(d["turns"] > 0 for d in dash["heatmap"])
    assert dash["sessions"][0]["session_id"] == sid
    assert dash["activity"]


def test_usage_api_requires_module(tmp_path) -> None:
    store.rebind(tmp_path / "usage-api.db")
    app.dependency_overrides[require_auth] = lambda: None
    client = TestClient(app)
    try:
        res = client.get("/api/usage")
        assert res.status_code == 200
        body = res.json()
        assert "heatmap" in body

        store.update_module("token_monitor", {"enabled": False})
        res = client.get("/api/usage")
        assert res.status_code == 404

        res = client.get("/usage", follow_redirects=False)
        assert res.status_code == 303
        assert res.headers["location"] == "/modules"
    finally:
        app.dependency_overrides.pop(require_auth, None)


def test_modules_list_api(tmp_path) -> None:
    store.rebind(tmp_path / "modules-api.db")
    app.dependency_overrides[require_auth] = lambda: None
    client = TestClient(app)
    try:
        res = client.get("/api/modules")
        assert res.status_code == 200
        ids = {m["id"] for m in res.json()["modules"]}
        assert {"token_monitor", "kanban"} <= ids
        assert "connector" not in ids
    finally:
        app.dependency_overrides.pop(require_auth, None)


def test_no_dispatch_when_module_disabled(tmp_path) -> None:
    store.rebind(tmp_path / "usage-off.db")
    store.update_module("token_monitor", {"enabled": False})
    admin = store.get_user_by_username("admin")
    sid = store.get_or_create_session("main", admin["id"])
    store.dispatch_turn_end(session_id=sid, agent_id="main", message="x")
    dash = store.with_db(ledger.dashboard)
    assert dash["summary"]["today"]["turns"] == 0
