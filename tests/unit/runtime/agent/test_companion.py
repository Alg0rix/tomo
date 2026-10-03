"""Companion diary: review details, bond stages, diary views, rhythm."""

from __future__ import annotations

import time
from datetime import date

from app.runtime.agent.learning import companion
from app.runtime.agent.learning.bond import STAGES, bond_breakdown, bond_stage, compute_bond
from app.runtime.agent.learning.diary import derive_diary, describe_learned
from app.runtime.agent.learning.memory_types import classify_review_action
from app.services import store


def _fact(content: str, *, action: str = "add", entity: str = "user/profile") -> dict:
    return classify_review_action(
        "memory",
        arguments={"action": action, "entity": entity, "content": content},
        result_text="Saved vault fact.",
    )


def test_classify_keeps_what_was_written() -> None:
    item = _fact("Prefers terse replies")
    assert item["saved_eligible"]
    assert item["detail"] == {"action": "add", "entity": "user/profile", "content": "Prefers terse replies"}
    skill = classify_review_action(
        "manage_skill",
        arguments={"action": "create", "skill_id": "rel", "display_name": "Release"},
        result_text="Created skill 'rel'.",
    )
    assert skill["detail"] == {"action": "create", "skill_id": "rel", "name": "Release"}
    episode = classify_review_action(
        "record_episode", arguments={"title": "ignored"}, result_text="Recorded episode ep_1: Shipped v2"
    )
    assert episode["detail"] == {"title": "Shipped v2"}
    assert "detail" not in classify_review_action("memory", arguments={"action": "search"}, result_text="x")


def test_describe_learned_and_derive_diary_prefer_prose() -> None:
    item = _fact("Prefers terse replies")
    assert describe_learned(item) == "Noted on your profile: Prefers terse replies"
    assert describe_learned({**item, "saved_eligible": False}) == ""
    # Explicit Diary: line wins; otherwise the items speak; raw actions last.
    assert derive_diary(saved=True, note="Diary: Learned your style.", actions=[], items=[item]) == "Learned your style."
    assert derive_diary(saved=True, note="", actions=["memory: Saved vault fact."], items=[item]).startswith(
        "Noted on your profile"
    )


def test_bond_breakdown_and_stage() -> None:
    values = dict(chats=40, saved_events=5, user_memory_chars=600, library_skills=1, days_active=10)
    parts = bond_breakdown(**values)
    assert [p["max"] for p in parts] == [25, 25, 20, 15, 15]
    assert all(0 <= p["ratio"] <= 1 for p in parts)
    bond = compute_bond(**values)
    stage = bond_stage(bond, parts)
    floor = max(s for s in (st[0] for st in STAGES) if s <= bond)
    assert stage["floor"] == floor
    assert stage["next"]["to_go"] == stage["next"]["at"] - bond
    assert stage["grow"]["key"] == max(parts, key=lambda p: p["max"] - p["points"])["key"]
    top = bond_stage(100, parts)
    assert top["next"] is None and top["kanji"] == "親友"


def test_event_view_new_legacy_and_skipped_rows() -> None:
    new = companion.event_view(
        {"id": 1, "saved": True, "diary": "", "session_id": "s1",
         "extract": {"items": [_fact("Uses uv", entity="project/tomo")]}},
        {"s1": "Release chat"},
    )
    assert new["status"] == "learned"
    want = {"kind": "fact", "verb": "noted", "entity": "project/tomo", "fact": "Uses uv", "href": "/memory#project/tomo"}
    assert want.items() <= new["learned"][0].items()
    assert new["session"] == {"id": "s1", "title": "Release chat", "exists": True}

    legacy = companion.event_view({
        "saved": True, "diary": "Recorded: memory: Saved vault fact.", "session_id": "gone",
        "extract": {"items": [{"tool": "memory", "saved_eligible": True}]},
    })
    assert legacy["learned"][0]["text"] == "Saved a note"
    assert not legacy["story"].startswith("Recorded:")
    assert legacy["session"]["exists"] is False

    skipped = companion.event_view({"saved": False, "note": "LLM request failed: empty choices"})
    assert skipped["status"] == "skipped" and skipped["session"] is None
    assert companion.event_view({"saved": False, "note": "nothing to save"})["status"] == "quiet"


def test_streaks_survive_until_day_ends() -> None:
    today = date(2026, 10, 3)
    active = {"2026-10-01", "2026-10-02", "2026-09-20", "2026-09-21", "2026-09-22"}
    assert companion._streaks(active, today) == (2, 3)
    assert companion._streaks(active | {"2026-10-03"}, today) == (3, 3)
    assert companion._streaks({"2026-09-30"}, today) == (0, 1)


def test_rhythm_buckets_by_local_day(tmp_path) -> None:
    store.rebind(tmp_path / "rhythm.db")
    counts = {"2026-10-02": {"chats": 2, "reviews": 1, "saves": 1}}
    r = companion.rhythm(counts, weeks=4, tz_minutes=0)
    assert r["days"][0]["weekday"] == 0
    hit = [d for d in r["days"] if d["date"] == "2026-10-02"]
    if hit:  # window is anchored to the real today
        assert hit[0]["intensity"] == 2 + 3 * 1
    assert companion.clamp_tz(10_000) == 840 and companion.clamp_tz(-10_000) == -840


def test_skills_together_ignores_bundled_catalog(tmp_path) -> None:
    store.rebind(tmp_path / "skills.db")

    def _run(conn):
        conn.execute("DELETE FROM skills")
        rows = [("bundled", "agents", 0), ("used", "claude", 4), ("mine", "library", 0)]
        for sid, source, uses in rows:
            conn.execute(
                "INSERT INTO skills (id, name, source, use_count, created_at) VALUES (?,?,?,?,?)",
                (sid, sid.title(), source, uses, time.time()),
            )
        return companion.skills_together(conn)

    out = store.with_db(_run)
    assert out["count"] == 2
    assert out["library"] == 1
    assert [s["id"] for s in out["most_used"]] == ["used"]


def test_diary_page_has_more_is_exact(tmp_path) -> None:
    store.rebind(tmp_path / "diary.db")
    for ts in (100.0, 200.0, 300.0):
        store.insert_learning_event(saved=ts != 200.0, diary="d", created_at=ts)
    page = store.companion_diary(user_id="web", limit=3)
    assert [e["created_at"] for e in page["entries"]] == [300.0, 200.0, 100.0]
    assert page["has_more"] is False
    learned = store.companion_diary(user_id="web", limit=1, learned_only=True)
    assert learned["has_more"] is True and learned["next_before"] == 300.0
