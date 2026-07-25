"""In-memory stub data store.

Stands in for the real coordinator/agent backend. Persists agent definitions and
chat sessions to app/data/store.json so the UI survives restarts.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any

from app.core.config import DATA_DIR
from app.services.platform_data import (
    seed_eval_domains,
    seed_eval_runs,
    seed_evaluators,
    seed_models,
    seed_plugins,
    seed_providers,
    seed_schedules,
    seed_settings,
    seed_shared_channels,
    seed_safety_rules,
    seed_skills,
    seed_tools,
    seed_users,
    seed_workplaces,
)

_LOCK = threading.RLock()


def _now() -> float:
    return time.time()


def _uuid_hex() -> str:
    return uuid.uuid4().hex[:8]


def _seed_agents() -> list[dict[str, Any]]:
    return [
        {"id": "main", "name": "Tomo", "description": "Coordinator agent — routes work across the swarm and handles direct chat.", "model_id": "gpt-4o-mini", "enabled": True, "is_super": True, "tool_count": 12, "channel_count": 3, "skill_count": 4, "busy": False, "created_at": _now() - 86400 * 14},
        {"id": "ops", "name": "Ops", "description": "Operations agent — deploys, runs shell tasks, watches workplaces.", "model_id": "claude-3.5-sonnet", "enabled": True, "is_super": False, "tool_count": 8, "channel_count": 1, "skill_count": 6, "busy": True, "created_at": _now() - 86400 * 9},
        {"id": "research", "name": "Research", "description": "Research agent — fetches, summarizes, and stores artifacts.", "model_id": "gpt-4o", "enabled": True, "is_super": False, "tool_count": 6, "channel_count": 1, "skill_count": 3, "busy": False, "created_at": _now() - 86400 * 5},
        {"id": "support", "name": "Support", "description": "Customer support agent — answers from the FAQ knowledge base.", "model_id": "gpt-4o-mini", "enabled": False, "is_super": False, "tool_count": 5, "channel_count": 2, "skill_count": 2, "busy": False, "created_at": _now() - 86400 * 2},
    ]


def _seed_sessions() -> list[dict[str, Any]]:
    base = _now()
    return [
        {
            "id": "ses_001",
            "agent_id": "main",
            "agent_ids": ["main", "ops", "research"],
            "coordinator_id": "main",
            "user_id": "web",
            "title": "Onboarding Q3 vendors",
            "message_count": 8,
            "updated_at": base - 3600,
            "created_at": base - 7200,
        },
        {
            "id": "ses_002",
            "agent_id": "ops",
            "agent_ids": ["ops"],
            "coordinator_id": "ops",
            "user_id": "web",
            "title": "Deploy staging cluster",
            "message_count": 23,
            "updated_at": base - 7200,
            "created_at": base - 9000,
        },
        {
            "id": "ses_003",
            "agent_id": "research",
            "agent_ids": ["research"],
            "coordinator_id": "research",
            "user_id": "web",
            "title": "Summarize competitor pricing",
            "message_count": 11,
            "updated_at": base - 18000,
            "created_at": base - 22000,
        },
    ]


class Store:
    """Thread-safe stub store backed by a JSON file."""

    def __init__(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._path = DATA_DIR / "store.json"
        self._busy: set[str] = set()
        self._state: dict[str, Any] = self._load()
        self._busy = set(self._state.get("busy_agent_ids", []))

    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            try:
                state = json.loads(self._path.read_text(encoding="utf-8"))
                self._migrate_state(state)
                if self._ensure_platform(state):
                    self._save(state)
                return state
            except Exception:
                pass
        state = {"agents": _seed_agents(), "sessions": _seed_sessions(), "history": {}}
        state.update({
            "tools": seed_tools(),
            "skills": seed_skills(),
            "plugins": seed_plugins(),
            "workplaces": seed_workplaces(),
            "schedules": seed_schedules(),
            "providers": seed_providers(),
            "models": seed_models(),
            "settings": seed_settings(),
            "safety_rules": seed_safety_rules(),
            "users": seed_users(),
            "shared_channels": seed_shared_channels(),
            "eval_domains": seed_eval_domains(),
            "evaluators": seed_evaluators(),
            "eval_runs": seed_eval_runs(),
        })
        self._save(state)
        return state

    def _ensure_platform(self, state: dict[str, Any]) -> bool:
        changed = False
        defaults = {
            "tools": seed_tools,
            "skills": seed_skills,
            "plugins": seed_plugins,
            "workplaces": seed_workplaces,
            "schedules": seed_schedules,
            "providers": seed_providers,
            "models": seed_models,
            "settings": seed_settings,
            "safety_rules": seed_safety_rules,
            "users": seed_users,
            "shared_channels": seed_shared_channels,
            "eval_domains": seed_eval_domains,
            "evaluators": seed_evaluators,
            "eval_runs": seed_eval_runs,
        }
        for key, factory in defaults.items():
            if key not in state:
                state[key] = factory()
                changed = True
        return changed

    def _migrate_state(self, state: dict[str, Any]) -> None:
        """Upgrade legacy single-agent sessions to swarm-capable shape."""
        changed = False
        for s in state.get("sessions", []):
            if "agent_ids" not in s:
                aid = s.get("agent_id")
                s["agent_ids"] = [aid] if aid else []
                s["coordinator_id"] = aid
                changed = True
            if "coordinator_id" not in s:
                s["coordinator_id"] = s.get("agent_id") or (s["agent_ids"][0] if s.get("agent_ids") else None)
                changed = True
            if s.get("agent_ids") and not s.get("agent_id"):
                s["agent_id"] = s["coordinator_id"] or s["agent_ids"][0]
                changed = True
        history = state.setdefault("history", {})
        for s in state.get("sessions", []):
            sid = s["id"]
            if sid in history:
                continue
            aid = s.get("coordinator_id") or s.get("agent_id")
            uid = s.get("user_id", "web")
            if not aid:
                continue
            legacy_key = self._hist_key(aid, uid)
            if legacy_key in history and isinstance(history[legacy_key], dict):
                legacy = history[legacy_key]
                if legacy.get("session_id") == sid:
                    history[sid] = {"entries": list(legacy.get("entries", []))}
                    changed = True
        if changed:
            self._save(state)

    def _save(self, state: dict[str, Any] | None = None) -> None:
        state = state or self._state
        snapshot = json.loads(json.dumps(state))
        snapshot["busy_agent_ids"] = list(self._busy)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def list_agents(self) -> list[dict[str, Any]]:
        with _LOCK:
            agents = [dict(a, busy=a["id"] in self._busy) for a in self._state["agents"]]
            agents.sort(key=lambda a: (not a["is_super"], a["created_at"]))
            return agents

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        with _LOCK:
            for a in self._state["agents"]:
                if a["id"] == agent_id:
                    return dict(a, busy=a["id"] in self._busy)
            return None

    def create_agent(self, data: dict[str, Any]) -> dict[str, Any]:
        with _LOCK:
            if any(a["id"] == data["id"] for a in self._state["agents"]):
                raise ValueError("Agent ID already exists")
            agent = {
                "id": data["id"],
                "name": data.get("name", data["id"]),
                "description": data.get("description", ""),
                "model_id": data.get("model_id"),
                "enabled": True,
                "is_super": False,
                "tool_count": 0,
                "channel_count": 0,
                "skill_count": 0,
                "busy": False,
                "created_at": _now(),
            }
            self._state["agents"].append(agent)
            self._save()
            return dict(agent)

    def update_agent(self, agent_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        with _LOCK:
            for a in self._state["agents"]:
                if a["id"] == agent_id:
                    for k in ("name", "description", "model_id", "enabled"):
                        if k in data and data[k] is not None:
                            a[k] = data[k]
                    self._save()
                    return dict(a, busy=a["id"] in self._busy)
            return None

    def delete_agent(self, agent_id: str) -> bool:
        with _LOCK:
            before = len(self._state["agents"])
            self._state["agents"] = [a for a in self._state["agents"] if a["id"] != agent_id]
            kept_sessions = []
            for s in self._state["sessions"]:
                ids = self._session_agent_ids(s)
                if agent_id not in ids:
                    kept_sessions.append(s)
                    continue
                remaining = [a for a in ids if a != agent_id]
                if remaining:
                    s["agent_ids"] = remaining
                    if s.get("coordinator_id") == agent_id:
                        s["coordinator_id"] = remaining[0]
                        s["agent_id"] = remaining[0]
                    kept_sessions.append(s)
            self._state["sessions"] = kept_sessions
            self._busy.discard(agent_id)
            changed = len(self._state["agents"]) != before
            if changed:
                self._save()
            return changed

    def set_busy(self, agent_id: str, busy: bool) -> None:
        with _LOCK:
            if busy:
                self._busy.add(agent_id)
            else:
                self._busy.discard(agent_id)
            self._save()

    def _hist_key(self, agent_id: str, user_id: str) -> str:
        return f"{agent_id}:{user_id}"

    def _session_agent_ids(self, session: dict[str, Any]) -> list[str]:
        return list(session.get("agent_ids") or ([session["agent_id"]] if session.get("agent_id") else []))

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with _LOCK:
            for s in self._state["sessions"]:
                if s["id"] == session_id:
                    return dict(s)
            return None

    def create_swarm_session(
        self,
        agent_ids: list[str],
        user_id: str = "web",
        coordinator_id: str | None = None,
    ) -> str:
        with _LOCK:
            ids = []
            for aid in agent_ids:
                if aid not in ids and self.get_agent(aid):
                    ids.append(aid)
            if not ids:
                raise ValueError("At least one valid agent is required")
            coord = coordinator_id if coordinator_id in ids else ids[0]
            for a in self._state["agents"]:
                if a.get("is_super") and a["id"] in ids:
                    coord = a["id"]
                    break
            sid = f"ses_{_uuid_hex()}"
            session = {
                "id": sid,
                "agent_id": coord,
                "agent_ids": ids,
                "coordinator_id": coord,
                "user_id": user_id,
                "title": "New swarm chat" if len(ids) > 1 else "New conversation",
                "message_count": 0,
                "updated_at": _now(),
                "created_at": _now(),
            }
            self._state["sessions"].insert(0, session)
            self._state["history"].setdefault(sid, {"entries": []})
            self._save()
            return sid

    def get_session_history(self, session_id: str) -> list[dict[str, Any]]:
        with _LOCK:
            hist = self._state["history"].get(session_id)
            if hist:
                return list(hist.get("entries", []))
            session = self.get_session(session_id)
            if not session:
                return []
            aid = session.get("coordinator_id") or session.get("agent_id")
            uid = session.get("user_id", "web")
            if aid:
                return list(self._state["history"].get(self._hist_key(aid, uid), {}).get("entries", []))
            return []

    def append_session_history(self, session_id: str, entry: dict[str, Any]) -> None:
        with _LOCK:
            hist = self._state["history"].setdefault(session_id, {"entries": []})
            entry.setdefault("ts", _now())
            hist["entries"].append(entry)
            for s in self._state["sessions"]:
                if s["id"] == session_id:
                    s["message_count"] = len(hist["entries"])
                    s["updated_at"] = _now()
                    if entry.get("type") == "user" and s["title"] in ("New conversation", "New swarm chat"):
                        s["title"] = (entry.get("content") or s["title"])[:60]
                    break
            self._save()

    def clear_session_by_id(self, session_id: str) -> None:
        with _LOCK:
            self._state["history"].pop(session_id, None)
            for s in self._state["sessions"]:
                if s["id"] == session_id:
                    s["message_count"] = 0
                    s["updated_at"] = _now()
                    break
            self._save()

    def update_session_agents(self, session_id: str, agent_ids: list[str]) -> dict[str, Any] | None:
        with _LOCK:
            for s in self._state["sessions"]:
                if s["id"] != session_id:
                    continue
                ids = []
                for aid in agent_ids:
                    if aid not in ids and self.get_agent(aid):
                        ids.append(aid)
                if not ids:
                    raise ValueError("At least one valid agent is required")
                s["agent_ids"] = ids
                if s.get("coordinator_id") not in ids:
                    s["coordinator_id"] = ids[0]
                    s["agent_id"] = ids[0]
                self._save()
                return dict(s)
            return None

    def get_or_create_session(self, agent_id: str, user_id: str) -> str:
        with _LOCK:
            key = self._hist_key(agent_id, user_id)
            hist = self._state["history"].setdefault(
                key,
                {"session_id": f"ses_{abs(hash(key)) % 100000:05d}", "entries": []},
            )
            sid = hist["session_id"]
            if not any(s["id"] == sid for s in self._state["sessions"]):
                self._state["sessions"].insert(
                    0,
                    {
                        "id": sid,
                        "agent_id": agent_id,
                        "agent_ids": [agent_id],
                        "coordinator_id": agent_id,
                        "user_id": user_id,
                        "title": "New conversation",
                        "message_count": 0,
                        "updated_at": _now(),
                        "created_at": _now(),
                    },
                )
            return sid

    def append_history(self, agent_id: str, user_id: str, entry: dict[str, Any]) -> None:
        with _LOCK:
            key = self._hist_key(agent_id, user_id)
            hist = self._state["history"].setdefault(
                key,
                {"session_id": f"ses_{abs(hash(key)) % 100000:05d}", "entries": []},
            )
            entry.setdefault("ts", _now())
            hist["entries"].append(entry)
            sid = hist["session_id"]
            for s in self._state["sessions"]:
                if s["id"] == sid:
                    s["message_count"] = len(hist["entries"])
                    s["updated_at"] = _now()
                    if entry.get("type") == "user" and s["title"] == "New conversation":
                        s["title"] = (entry.get("content") or "New conversation")[:60]
                    break
            self._save()

    def get_history(self, agent_id: str, user_id: str) -> list[dict[str, Any]]:
        with _LOCK:
            key = self._hist_key(agent_id, user_id)
            return list(self._state["history"].get(key, {}).get("entries", []))

    def clear_session(self, agent_id: str, user_id: str) -> None:
        with _LOCK:
            key = self._hist_key(agent_id, user_id)
            self._state["history"].pop(key, None)
            self._save()

    def list_sessions(self) -> list[dict[str, Any]]:
        with _LOCK:
            sessions = sorted(self._state["sessions"], key=lambda s: s["updated_at"], reverse=True)
            return [dict(s) for s in sessions]

    def stats(self) -> dict[str, Any]:
        with _LOCK:
            agents = self._state["agents"]
            sessions = self._state["sessions"]
            enabled = [a for a in agents if a["enabled"]]
            workplaces = self._state.get("workplaces", [])
            return {
                "agent_count": len(agents),
                "enabled_agent_count": len(enabled),
                "session_count": len(sessions),
                "tool_count": len(self._state.get("tools", [])) or sum(a["tool_count"] for a in agents),
                "channel_count": sum(a["channel_count"] for a in agents),
                "active_channel_count": sum(a["channel_count"] for a in enabled),
                "skill_count": len(self._state.get("skills", [])) or sum(a["skill_count"] for a in agents),
                "workplace_count": len(workplaces),
            }

    def dashboard_data(self) -> dict[str, Any]:
        with _LOCK:
            agents = sorted(self._state["agents"], key=lambda a: a["created_at"], reverse=True)
            sessions = sorted(self._state["sessions"], key=lambda s: s["updated_at"], reverse=True)
            return {
                "stats": self.stats(),
                "recent_agents": [dict(a, busy=a["id"] in self._busy) for a in agents[:5]],
                "recent_sessions": [dict(s) for s in sessions[:6]],
                "busy_agents": [a["id"] for a in agents if a["id"] in self._busy],
                "workplaces": self.list_workplaces()[:3],
                "schedules": self.list_schedules()[:3],
            }

    # ── Platform entities (stub) ─────────────────────────────────────

    def list_tools(self) -> list[dict[str, Any]]:
        with _LOCK:
            return [dict(t) for t in self._state.get("tools", [])]

    def list_skills(self) -> list[dict[str, Any]]:
        with _LOCK:
            return [dict(s) for s in self._state.get("skills", [])]

    def get_skill(self, skill_id: str) -> dict[str, Any] | None:
        with _LOCK:
            for s in self._state.get("skills", []):
                if s["id"] == skill_id:
                    return dict(s)
            return None

    def list_plugins(self) -> list[dict[str, Any]]:
        with _LOCK:
            return [dict(p) for p in self._state.get("plugins", [])]

    def get_plugin(self, plugin_id: str) -> dict[str, Any] | None:
        with _LOCK:
            for p in self._state.get("plugins", []):
                if p["id"] == plugin_id:
                    return dict(p)
            return None

    def list_workplaces(self) -> list[dict[str, Any]]:
        with _LOCK:
            return [dict(w) for w in self._state.get("workplaces", [])]

    def get_workplace(self, workplace_id: str) -> dict[str, Any] | None:
        with _LOCK:
            for w in self._state.get("workplaces", []):
                if w["id"] == workplace_id:
                    return dict(w)
            return None

    def list_schedules(self) -> list[dict[str, Any]]:
        with _LOCK:
            return [dict(s) for s in self._state.get("schedules", [])]

    def get_schedule(self, schedule_id: str) -> dict[str, Any] | None:
        with _LOCK:
            for s in self._state.get("schedules", []):
                if s["id"] == schedule_id:
                    return dict(s)
            return None

    def list_models(self) -> list[dict[str, Any]]:
        with _LOCK:
            return [dict(m) for m in self._state.get("models", [])]

    def list_providers(self) -> list[dict[str, Any]]:
        with _LOCK:
            return [dict(p) for p in self._state.get("providers", [])]

    def get_settings(self) -> dict[str, Any]:
        with _LOCK:
            return dict(self._state.get("settings", seed_settings()))

    def update_settings(self, data: dict[str, Any]) -> dict[str, Any]:
        with _LOCK:
            settings = self._state.setdefault("settings", seed_settings())
            settings.update({k: v for k, v in data.items() if v is not None})
            self._save()
            return dict(settings)

    def is_setup_complete(self) -> bool:
        return bool(self.get_settings().get("setup_complete", True))

    def get_agent_tools(self, agent_id: str) -> list[dict[str, Any]]:
        tools = self.list_tools()
        agent = self.get_agent(agent_id)
        count = (agent or {}).get("tool_count", 0)
        return [dict(t, enabled=i < count) for i, t in enumerate(tools)]

    def get_agent_skills(self, agent_id: str) -> list[dict[str, Any]]:
        skills = self.list_skills()
        names = {"main": ["onboarding", "deploy"], "ops": ["deploy"], "research": ["research_brief"]}
        assigned = set(names.get(agent_id, []))
        return [dict(s, assigned=s["id"] in assigned) for s in skills]

    def get_agent_channels(self, agent_id: str) -> list[dict[str, Any]]:
        base = [
            {"id": "web", "type": "web", "name": "Web UI", "status": "active"},
            {"id": "telegram", "type": "telegram", "name": "Telegram", "status": "planned"},
            {"id": "whatsapp", "type": "whatsapp", "name": "WhatsApp", "status": "planned"},
        ]
        agent = self.get_agent(agent_id)
        n = (agent or {}).get("channel_count", 1)
        return [dict(c, enabled=i < n) for i, c in enumerate(base)]

    def list_safety_rules(self) -> list[dict[str, Any]]:
        with _LOCK:
            return [dict(r) for r in self._state.get("safety_rules", [])]

    def list_users(self) -> list[dict[str, Any]]:
        with _LOCK:
            return [dict(u) for u in self._state.get("users", [])]

    def list_shared_channels(self) -> list[dict[str, Any]]:
        with _LOCK:
            return [dict(c) for c in self._state.get("shared_channels", [])]

    def list_eval_domains(self) -> list[dict[str, Any]]:
        with _LOCK:
            return [dict(d) for d in self._state.get("eval_domains", [])]

    def list_evaluators(self) -> list[dict[str, Any]]:
        with _LOCK:
            return [dict(e) for e in self._state.get("evaluators", [])]

    def list_eval_runs(self) -> list[dict[str, Any]]:
        with _LOCK:
            return sorted(self._state.get("eval_runs", []), key=lambda r: r.get("started_at", 0), reverse=True)

    def get_eval_run(self, run_id: str) -> dict[str, Any] | None:
        with _LOCK:
            for r in self._state.get("eval_runs", []):
                if r["id"] == run_id:
                    return dict(r)
            return None


store = Store()
