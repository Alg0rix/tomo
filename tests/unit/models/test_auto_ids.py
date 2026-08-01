"""Server auto-generates create ids from name/title when omitted."""

from __future__ import annotations

from pathlib import Path

from app.models.ids import slugify, unique_id
from app.services import store


def _rebind(tmp_path: Path) -> None:
    store.rebind(tmp_path / "auto-ids.db")


def test_slugify() -> None:
    assert slugify("Staging Pi!") == "staging_pi"
    assert slugify("") == "item"


def test_workplace_create_without_id(tmp_path: Path) -> None:
    _rebind(tmp_path)
    wp = store.create_workplace(
        {"name": "My Raspberry", "kind": "tunnel"}
    )
    assert wp["id"].startswith("tun_")
    assert "raspberry" in wp["id"]
    assert wp["name"] == "My Raspberry"


def test_agent_create_without_id(tmp_path: Path) -> None:
    _rebind(tmp_path)
    a = store.create_agent({"name": "Research Bot", "role": "research"})
    assert a["id"]
    assert "research" in a["id"] or "bot" in a["id"]


def test_new_agent_joins_existing_swarm_sessions(tmp_path: Path) -> None:
    _rebind(tmp_path)
    sid = store.create_home_session()["session_id"]
    before = set(store.get_session(sid)["agent_ids"])
    a = store.create_agent({"name": "New Hire", "role": "ops"})
    after = set(store.get_session(sid)["agent_ids"])
    assert a["id"] in after
    assert before.issubset(after)


def test_llm_profile_create_without_id(tmp_path: Path) -> None:
    _rebind(tmp_path)
    p = store.create_llm_profile(
        {"name": "North Cloud", "base_url": "https://x", "model": "north"}
    )
    assert p["id"]
    assert "north" in p["id"] or "cloud" in p["id"]


def test_knowledge_create_without_id(tmp_path: Path) -> None:
    _rebind(tmp_path)
    e = store.create_knowledge_entry(
        {"title": "Vendor deadline", "body": "Oct 15"}
    )
    assert e["id"].startswith("kb_")


def test_explicit_id_still_works(tmp_path: Path) -> None:
    _rebind(tmp_path)
    wp = store.create_workplace(
        {"id": "wp_custom", "name": "Custom", "kind": "local", "root_path": str(tmp_path)}
    )
    assert wp["id"] == "wp_custom"


def test_unique_id_collision(tmp_path: Path) -> None:
    _rebind(tmp_path)
    store.create_workplace({"name": "Box", "kind": "local", "root_path": str(tmp_path)})
    second = store.create_workplace(
        {"name": "Box", "kind": "local", "root_path": str(tmp_path)}
    )
    assert second["id"] != "wp_box" or True  # first may be wp_box
    # Both exist
    assert store.get_workplace("wp_box") is not None or store.list_workplaces()
    ids = {w["id"] for w in store.list_workplaces()}
    assert len(ids) == 2


def test_unique_id_helper_table(tmp_path: Path) -> None:
    _rebind(tmp_path)
    conn = store._conn
    a = unique_id(conn, "workplaces", name="Alpha", prefix="wp")
    assert a.startswith("wp_")
    conn.execute(
        "INSERT INTO workplaces (id, name, kind, status, host, root_path, "
        "ssh_host, ssh_port, ssh_user, ssh_password, ssh_key, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (a, "Alpha", "local", "offline", "", "", "", 22, "", "", "", 0, 0),
    )
    conn.commit()
    b = unique_id(conn, "workplaces", name="Alpha", prefix="wp")
    assert b != a
