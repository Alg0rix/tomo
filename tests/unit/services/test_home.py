"""家 Home: per-user scoping, plugin card contract, and layout API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.deps import require_auth
from app.services import home, store


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from app.core import config

    store.rebind(tmp_path / "home.db")
    monkeypatch.setattr(config, "TOMO_HOME", tmp_path / "tomo-home")
    yield


@pytest.fixture
def pending_approvals():
    from app.runtime.permissions import hitl

    added: list[str] = []

    def add(session_id: str, **payload):
        pid = f"apr_{len(added)}"
        hitl._approvals[pid] = hitl._PendingApproval(
            id=pid,
            session_id=session_id,
            payload={"id": pid, "tool": "shell", "choices": ["once", "session", "deny"], **payload},
        )
        added.append(pid)
        return pid

    yield add
    for pid in added:
        hitl._approvals.pop(pid, None)


def test_needs_and_recent_are_scoped_to_user(pending_approvals):
    alice = store.create_swarm_session(["main"], user_id="usr_alice")
    bob = store.create_swarm_session(["main"], user_id="usr_bob")
    pending_approvals(alice, args_preview={"command": "rm -rf build"})
    pending_approvals(bob, args_preview={"command": "cat secrets"})

    data = home.live_snapshot("usr_alice")

    assert [n["chat"]["id"] for n in data["needs"]] == [alice]
    need = data["needs"][0]
    assert need["kind"] == "approval"
    assert need["title"] == "rm -rf build"
    assert need["choices"] == ["once", "deny"]
    assert {c["id"] for c in data["recent"]} == {alice}
    assert {c["id"] for c in home.live_snapshot("usr_bob")["recent"]} == {bob}


def test_snapshot_has_every_section():
    data = home.snapshot("usr_alice", 420)
    assert set(data) >= {
        "coordinator", "needs", "live", "today", "foundations",
        "household", "recent", "rooms", "starters", "layout",
    }
    keys = {r["key"] for r in data["rooms"]}
    assert {"core:memory", "core:companion"} <= keys
    assert {f["key"] for f in data["foundations"]} >= {"brain", "memory", "version"}


def test_normalize_card_clamps_and_drops_unsafe_values():
    card = home.normalize_card(
        {
            "metric": {"value": 12345, "label": "spent"},
            "bars": [{"label": "Food", "value": 4, "tone": "neon"}] * 9,
            "list": [{"label": "x", "href": "https://evil.example"}, {"label": "y", "href": "/plugins/money/"}],
            "spark": [1, "2", None, 3],
            "actions": [{"label": "Ask", "prompt": "hi"}, {"label": "Go", "href": "//evil"}, {"nope": 1}],
            "html": "<script>alert(1)</script>",
        }
    )
    assert card["metric"] == {"value": "12345", "label": "spent"}
    assert len(card["bars"]) == 5
    assert card["bars"][0]["value"] == 1.0 and card["bars"][0]["tone"] == ""
    assert [r["href"] for r in card["list"]] == ["", "/plugins/money/"]
    assert "html" not in card
    assert card["actions"] == [{"label": "Ask", "prompt": "hi"}]


def test_normalize_card_rich_fields():
    card = home.normalize_card(
        {
            "status": {"text": "over budget", "tone": "hot"},
            "stats": [{"label": "Open", "value": 5, "trend": {"text": "+2", "direction": "up"}}, {"label": "x"}] * 3,
            "chart": [{"label": f"d{i}", "value": i, "tone": "warm" if i == 39 else "neon"} for i in range(40)],
            "ring": {"value": "62%", "segments": [{"label": "Food", "value": 3}, {"label": "Zero", "value": 0}, {"label": "Bad", "value": "x"}]},
            "heatmap": {"values": [0, 1, True, "2", 3] * 40, "label": "20 weeks"},
            "timeline": [{"time": "09:00", "label": "Standup", "href": "javascript:alert(1)", "tone": "info"}],
            "checklist": [{"label": "Buy milk", "done": 1}, {"done": True}],
            "tags": [{"label": "urgent", "tone": "hot"}] * 10,
            "list": [{"label": "Rent", "sub": "due Fri", "who": {"name": "Kai", "agent": "kai"}, "tone": "warm"}],
            "columns": [
                {"title": "Doing", "items": [{"title": "Auth", "who": {"name": "Tomo", "agent": "../etc"}}]},
                {"title": "Done", "muted": True, "count": True, "items": []},
            ],
        }
    )
    assert card["status"] == {"text": "over budget", "tone": "hot"}
    assert len(card["stats"]) == 3 and card["stats"][0] == {
        "label": "Open", "value": "5", "trend": {"text": "+2", "direction": "up", "good": False},
    }
    assert len(card["chart"]) == 31 and card["chart"][-1] == {"label": "d39", "value": 39.0, "tone": "warm"}
    assert "tone" not in card["chart"][0]
    assert card["ring"] == {"segments": [{"label": "Food", "value": 3.0}], "value": "62%"}
    assert len(card["heatmap"]["values"]) == 120 and 2 not in card["heatmap"]["values"]
    assert card["timeline"] == [{"time": "09:00", "label": "Standup", "tone": "info"}]
    assert card["checklist"] == [{"label": "Buy milk", "done": True}]
    assert len(card["tags"]) == 8
    assert card["list"][0]["who"] == {"name": "Kai", "agent": "kai"} and card["list"][0]["sub"] == "due Fri"
    doing, done = card["columns"]
    assert doing["items"][0]["who"] == {"name": "Tomo"} and "muted" not in doing
    assert done["muted"] is True and done["count"] == 0


def test_normalize_card_extended_fields():
    card = home.normalize_card(
        {
            "notice": {"text": "Gmail token expired", "tone": "hot"},
            "image": {"src": "https://evil.example/x.png", "alt": "x"},
            "steps": [
                {"label": "Fetched", "state": "done"},
                {"label": "Importing", "state": "active"},
                {"label": "Verify", "state": "weird"},
                {"nope": 1},
            ] * 2,
            "table": {
                "columns": ["Date", "Item", "Amt", "x", "extra"],
                "rows": [["10-01", "Coffee", "42", "orphan", "dropped"], "not-a-row", ["10-02", "Tea"]],
            },
            "code": "first\n" + "x\n" * 10,
            "foot": "updated 10:32 · 3 sources",
        }
    )
    assert card["notice"] == {"text": "Gmail token expired", "tone": "hot"}
    assert "image" not in card
    assert card["steps"] == [
        {"label": "Fetched", "state": "done"},
        {"label": "Importing", "state": "active"},
        {"label": "Verify"},
        {"label": "Fetched", "state": "done"},
        {"label": "Importing", "state": "active"},
    ]
    assert card["table"]["columns"] == ["Date", "Item", "Amt", "x"]
    assert card["table"]["rows"] == [
        ["10-01", "Coffee", "42", "orphan"],
        ["10-02", "Tea", "", ""],
    ]
    assert card["code"].splitlines() == ["first", "x", "x", "x", "x", "x"]
    assert card["foot"] == "updated 10:32 · 3 sources"

    image = home.normalize_card({"image": {"src": "/plugins/money/static/r.png", "alt": "receipt"}})
    assert image["image"] == {"src": "/plugins/money/static/r.png", "alt": "receipt"}


def test_normalize_card_dashboard_fields():
    card = home.normalize_card(
        {
            "metric": {"value": "87%", "label": "cpu", "tone": "hot"},
            "stats": [{"label": "p99", "value": "410ms", "tone": "warm"}],
            "series": {
                "labels": ["00:00", "now"],
                "lines": [
                    {"label": "cpu", "values": [1, -2, 3, "x", 4], "tone": "warm"},
                    {"values": [0]},
                    "junk",
                ],
            },
            "gauge": {"value": 0.7, "label": "memory", "text": "70%", "tone": "warm"},
            "states": [
                {"tone": "ok", "value": 20, "text": "up"},
                {"tone": "hot", "value": -3},
                {"tone": "warm"},
                {"bad": 1},
            ],
        }
    )
    assert card["metric"] == {"value": "87%", "label": "cpu", "tone": "hot"}
    assert card["stats"][0]["tone"] == "warm"
    assert card["series"] == {
        "lines": [{"values": [1.0, -2.0, 3.0, 4.0], "label": "cpu", "tone": "warm"}],
        "labels": ["00:00", "now"],
    }
    assert card["gauge"] == {"value": 0.7, "label": "memory", "text": "70%", "tone": "warm"}
    assert card["states"] == [
        {"tone": "ok", "value": 20.0, "text": "up"},
        {"tone": "hot", "value": 1},
        {"tone": "warm", "value": 1},
        {"tone": "", "value": 1},
    ]


def test_normalize_card_rejects_non_dict():
    with pytest.raises(ValueError):
        home.normalize_card(["not", "a", "card"])


def test_layout_api_roundtrip_and_isolation(monkeypatch):
    from app.api import rest
    from app.main import app

    user = {"id": "usr_alice"}
    monkeypatch.setattr(rest, "session_user_id", lambda request: user["id"])
    app.dependency_overrides[require_auth] = lambda: None
    try:
        client = TestClient(app)
        r = client.put("/api/home/layout", json={"order": ["core:memory", 7, "money:0"], "hidden": ["core:companion"]})
        assert r.status_code == 200
        assert r.json() == {"order": ["core:memory", "money:0"], "hidden": ["core:companion"]}
        assert client.get("/api/home?tz=0").json()["layout"]["hidden"] == ["core:companion"]
        user["id"] = "usr_bob"
        assert client.get("/api/home/live").status_code == 200
        assert client.get("/api/home/badges").json() == {"needs": 0, "running": 0}
        assert client.get("/api/home?tz=0").json()["layout"] == {"order": [], "hidden": []}
        assert client.get("/api/home?tz=9999").status_code == 422
    finally:
        app.dependency_overrides.pop(require_auth, None)


def test_rail_renders_rooms_with_kanji_and_tint():
    from app.core.deps import templates

    html = templates.get_template("partials/app_rail.html").render(
        page="home",
        brand="Tomo",
        current_username="ana",
        current_role="admin",
        plugin_nav=[
            {"id": "money", "label": "Money", "path": "/plugins/money/", "page": "plugin-money", "icon": "wallet", "kanji": "金", "tint": "ok"},
            {"id": "notes", "label": "Notes", "path": "/plugins/notes/", "page": "plugin-notes", "icon": "puzzle", "kanji": "", "tint": "info"},
        ],
        url_for=lambda *a, **k: "/static/mark.png",
    )
    assert 'app-rail-room ok"><span lang="ja">金</span>' in html
    assert 'app-rail-room info">' in html and 'data-plugin-icon="puzzle"' in html
    assert "Under the roof" in html and 'data-rail-badge="needs"' in html
    assert "admin · " in html and ">A</span>" in html
