"""Phase 3 episodic: graph, contradictions, consolidation, boundaries (SQLite only)."""

from __future__ import annotations

from app.runtime.memory import episodes as ep_svc
from app.services import store


def _rebind(tmp_path) -> None:
    store.rebind(tmp_path / "ep3.db")


def test_experience_graph_auto_link(tmp_path) -> None:
    _rebind(tmp_path)
    a = store.insert_episode(
        {
            "user_id": "u",
            "objective": "restore service payments",
            "context_summary": "prod k8s",
            "trajectory_summary": "restarted dependency payments-db",
            "outcome_status": "success",
            "outcome_summary": "healthy",
            "importance": 0.8,
            "confidence": 0.8,
            "utility": 0.8,
            "force": True,
        }
    )
    b = store.insert_episode(
        {
            "user_id": "u",
            "objective": "restore service payments after crash",
            "context_summary": "prod k8s cluster",
            "trajectory_summary": "restarted dependency payments-db again",
            "outcome_status": "success",
            "outcome_summary": "recovered",
            "importance": 0.8,
            "confidence": 0.8,
            "utility": 0.8,
            "force": True,
        }
    )
    assert a and b and a["id"] != b["id"]
    hits = store.search_episodes("payments restore", user_id="u", limit=5)
    assert hits
    # Graph expand may attach related ids
    related = hits[0].get("related") or []
    assert isinstance(related, list)


def test_contradictions_detected(tmp_path) -> None:
    _rebind(tmp_path)
    store.insert_episode(
        {
            "user_id": "u",
            "objective": "deploy billing service",
            "context_summary": "staging",
            "outcome_status": "success",
            "outcome_summary": "blue-green worked",
            "force": True,
            "importance": 0.7,
        }
    )
    store.insert_episode(
        {
            "user_id": "u",
            "objective": "deploy billing service",
            "context_summary": "production",
            "outcome_status": "failure",
            "outcome_summary": "blue-green caused outage",
            "force": True,
            "importance": 0.9,
        }
    )
    contras = ep_svc.contradictions(user_id="u", limit=10)
    assert contras
    assert any(
        c["outcome_a"] != c["outcome_b"] for c in contras
    )


def test_semantic_and_procedural_consolidation(tmp_path) -> None:
    _rebind(tmp_path)
    store.insert_episode(
        {
            "user_id": "u",
            "title": "CI flake",
            "objective": "fix flaky integration test",
            "trajectory_summary": "set seed; rerun; pin dependency",
            "actions": ["set random seed", "rerun suite", "pin dependency version"],
            "outcome_status": "success",
            "outcome_summary": "CI green",
            "reflection_summary": "Always pin flaky dependency versions",
            "lessons": ["Pin dependency versions when CI is flaky"],
            "importance": 0.9,
            "confidence": 0.9,
            "utility": 0.9,
            "force": True,
        }
    )
    # Seed reuse so consolidation gate passes.
    eps = store.list_episodes(user_id="u", limit=5)
    assert eps
    store.episode_feedback(eps[0]["id"], helpful=True, user_id="u")
    store.episode_feedback(eps[0]["id"], helpful=True, user_id="u")

    sem = ep_svc.consolidate_semantic(user_id="u", min_reuse=2)
    assert sem
    kb = store.list_knowledge_entries(user_id="u")
    assert any("from-episodic" in (e.get("tags") or []) for e in kb)

    procs = ep_svc.extract_procedures(user_id="u", min_success=0)
    assert procs
    kb2 = store.list_knowledge_entries(user_id="u")
    assert any("procedural" in (e.get("tags") or []) for e in kb2)


def test_boundaries_open_close(tmp_path) -> None:
    _rebind(tmp_path)
    ep = ep_svc.open_episode(
        session_id="ses_1",
        user_id="u",
        agent_id="main",
        objective="Investigate outage",
    )
    assert ep is not None
    assert ep_svc.active_episode_id("ses_1") == ep["id"]
    assert ep_svc.append_to_open(
        "ses_1",
        event={"type": "observation", "description": "error rate spiked"},
        user_id="u",
    )
    closed = ep_svc.close_episode(
        "ses_1",
        outcome_status="success",
        outcome_summary="mitigated",
        reflection="scale replicas first",
    )
    assert closed is not None
    assert ep_svc.active_episode_id("ses_1") is None
    assert closed.get("outcome_status") == "success"


def test_cross_agent_retrieval(tmp_path) -> None:
    _rebind(tmp_path)
    store.insert_episode(
        {
            "user_id": "u",
            "agent_id": "ops",
            "objective": "rotate certs",
            "outcome_summary": "renewed with certbot",
            "outcome_status": "success",
            "force": True,
        }
    )
    store.insert_episode(
        {
            "user_id": "u",
            "agent_id": "coder",
            "objective": "fix type error",
            "outcome_summary": "cast fixed",
            "outcome_status": "success",
            "force": True,
        }
    )
    # Default cross-agent for same user.
    hits = store.search_episodes("certbot renew", user_id="u", limit=5)
    assert hits
    assert hits[0]["agent_id"] in {"ops", "coder", ""}


def test_optimize_ltm(tmp_path) -> None:
    _rebind(tmp_path)
    store.insert_episode(
        {
            "user_id": "u",
            "objective": "task",
            "actions": ["step one", "step two", "step three"],
            "trajectory_summary": "step one; step two; step three",
            "outcome_status": "success",
            "outcome_summary": "done",
            "lessons": ["Do steps in order"],
            "importance": 0.9,
            "confidence": 0.9,
            "utility": 0.9,
            "force": True,
        }
    )
    eps = store.list_episodes(user_id="u")
    store.episode_feedback(eps[0]["id"], helpful=True, user_id="u")
    store.episode_feedback(eps[0]["id"], helpful=True, user_id="u")
    result = ep_svc.optimize_ltm(user_id="u")
    assert "decay" in result
    assert result["semantic_facts"] >= 0
