"""Thin store facade: SQLite for agents/sessions/messages/settings and
``platform_data`` for the remaining platform lists (tools, skills, plugins,
workplaces, schedules, models, providers, safety rules, users, shared
channels, eval_*).

Public method names match the previous JSON-backed store so the API/UI layer
is unchanged. Busy state is process-local in-memory
(:class:`app.models.mixins.busy.BusyState`) — never persisted to SQLite.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from app.core.config import DB_PATH
from app.models.db import get_connection
from app.models.mixins import agents as agents_store
from app.models.mixins import messages as messages_store
from app.models.mixins import sessions as sessions_store
from app.models.mixins import settings as settings_store
from app.models.mixins.busy import BusyState
from app.models.schema import migrate
from app.models.seed import seed_if_empty
from app.services.platform_data import (
    seed_eval_domains,
    seed_eval_runs,
    seed_evaluators,
    seed_models,
    seed_plugins,
    seed_providers,
    seed_schedules,
    seed_safety_rules,
    seed_shared_channels,
    seed_skills,
    seed_tools,
    seed_users,
    seed_workplaces,
)


class Store:
    """Hybrid facade: SQLite for core entities, platform_data for lists."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._lock = threading.RLock()
        self._busy = BusyState()
        self._path = path
        self._platform = self._seed_platform()
        self._open()

    # -- lifecycle -------------------------------------------------------
    def _open(self) -> None:
        path = self._path if self._path is not None else DB_PATH
        self._conn = get_connection(path)
        migrate(self._conn)
        seed_if_empty(self._conn)

    def rebind(self, path: str | Path | None) -> None:
        """Reopen against ``path`` (temp DB in tests); re-migrate + re-seed."""
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
            self._path = path
            self._busy = BusyState()
            self._open()

    def _seed_platform(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "tools": seed_tools(),
            "skills": seed_skills(),
            "plugins": seed_plugins(),
            "workplaces": seed_workplaces(),
            "schedules": seed_schedules(),
            "providers": seed_providers(),
            "models": seed_models(),
            "safety_rules": seed_safety_rules(),
            "users": seed_users(),
            "shared_channels": seed_shared_channels(),
            "eval_domains": seed_eval_domains(),
            "evaluators": seed_evaluators(),
            "eval_runs": seed_eval_runs(),
        }

    # -- agents (SQLite) -------------------------------------------------
    def list_agents(self) -> list[dict[str, Any]]:
        with self._lock:
            return agents_store.list_agents(self._conn, self._busy.ids())

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        with self._lock:
            return agents_store.get_agent(self._conn, agent_id, self._busy.ids())

    def create_agent(self, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            return agents_store.create_agent(self._conn, data)

    def update_agent(self, agent_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            return agents_store.update_agent(self._conn, agent_id, data, self._busy.ids())

    def delete_agent(self, agent_id: str) -> bool:
        with self._lock:
            ok = agents_store.delete_agent(self._conn, agent_id)
            if ok:
                self._busy.set_busy(agent_id, False)
            return ok

    def set_busy(self, agent_id: str, busy: bool) -> None:
        with self._lock:
            self._busy.set_busy(agent_id, busy)

    # -- sessions (SQLite) -----------------------------------------------
    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            return sessions_store.get_session(self._conn, session_id)

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            return sessions_store.list_sessions(self._conn)

    def create_swarm_session(
        self, agent_ids: list[str], user_id: str = "web", coordinator_id: str | None = None
    ) -> str:
        with self._lock:
            return sessions_store.create_swarm_session(self._conn, agent_ids, user_id, coordinator_id)

    def update_session_agents(self, session_id: str, agent_ids: list[str]) -> dict[str, Any] | None:
        with self._lock:
            return sessions_store.update_session_agents(self._conn, session_id, agent_ids)

    def get_or_create_session(self, agent_id: str, user_id: str) -> str:
        with self._lock:
            return sessions_store.get_or_create_session(self._conn, agent_id, user_id)

    # -- messages / history (SQLite) -------------------------------------
    def get_session_history(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return messages_store.get_session_history(self._conn, session_id)

    def append_session_history(self, session_id: str, entry: dict[str, Any]) -> None:
        with self._lock:
            messages_store.append_session_history(self._conn, session_id, entry)

    def clear_session_by_id(self, session_id: str) -> None:
        with self._lock:
            messages_store.clear_session_history(self._conn, session_id)

    def append_history(self, agent_id: str, user_id: str, entry: dict[str, Any]) -> None:
        with self._lock:
            sid = sessions_store.get_or_create_session(self._conn, agent_id, user_id)
            messages_store.append_session_history(self._conn, sid, entry)

    def get_history(self, agent_id: str, user_id: str) -> list[dict[str, Any]]:
        with self._lock:
            sid = sessions_store.get_or_create_session(self._conn, agent_id, user_id)
            return messages_store.get_session_history(self._conn, sid)

    def clear_session(self, agent_id: str, user_id: str) -> None:
        with self._lock:
            sid = sessions_store.get_or_create_session(self._conn, agent_id, user_id)
            messages_store.clear_session_history(self._conn, sid)

    # -- stats / dashboard -----------------------------------------------
    def stats(self) -> dict[str, Any]:
        agents = self.list_agents()
        enabled = [a for a in agents if a["enabled"]]
        return {
            "agent_count": len(agents),
            "enabled_agent_count": len(enabled),
            "session_count": len(self.list_sessions()),
            "tool_count": len(self._platform["tools"]) or sum(a["tool_count"] for a in agents),
            "channel_count": sum(a["channel_count"] for a in agents),
            "active_channel_count": sum(a["channel_count"] for a in enabled),
            "skill_count": len(self._platform["skills"]) or sum(a["skill_count"] for a in agents),
            "workplace_count": len(self._platform["workplaces"]),
        }

    def dashboard_data(self) -> dict[str, Any]:
        agents = self.list_agents()
        sessions = self.list_sessions()
        return {
            "stats": self.stats(),
            "recent_agents": agents[:5],
            "recent_sessions": sessions[:6],
            "busy_agents": [a["id"] for a in agents if a["busy"]],
            "workplaces": [dict(w) for w in self._platform["workplaces"][:3]],
            "schedules": [dict(s) for s in self._platform["schedules"][:3]],
        }

    # -- settings (SQLite) -----------------------------------------------
    def get_settings(self) -> dict[str, Any]:
        with self._lock:
            return settings_store.get_settings(self._conn)

    def update_settings(self, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            return settings_store.update_settings(self._conn, data)

    def is_setup_complete(self) -> bool:
        return bool(self.get_settings().get("setup_complete", True))

    # -- platform lists (platform_data) ----------------------------------
    def _plat(self, key: str) -> list[dict[str, Any]]:
        return [dict(x) for x in self._platform[key]]

    def list_tools(self) -> list[dict[str, Any]]:
        return self._plat("tools")

    def list_skills(self) -> list[dict[str, Any]]:
        return self._plat("skills")

    def list_plugins(self) -> list[dict[str, Any]]:
        return self._plat("plugins")

    def list_workplaces(self) -> list[dict[str, Any]]:
        return self._plat("workplaces")

    def list_schedules(self) -> list[dict[str, Any]]:
        return self._plat("schedules")

    def list_models(self) -> list[dict[str, Any]]:
        return self._plat("models")

    def list_providers(self) -> list[dict[str, Any]]:
        return self._plat("providers")

    def list_safety_rules(self) -> list[dict[str, Any]]:
        return self._plat("safety_rules")

    def list_users(self) -> list[dict[str, Any]]:
        return self._plat("users")

    def list_shared_channels(self) -> list[dict[str, Any]]:
        return self._plat("shared_channels")

    def list_eval_domains(self) -> list[dict[str, Any]]:
        return self._plat("eval_domains")

    def list_evaluators(self) -> list[dict[str, Any]]:
        return self._plat("evaluators")

    def list_eval_runs(self) -> list[dict[str, Any]]:
        return sorted(self._plat("eval_runs"), key=lambda r: r.get("started_at", 0), reverse=True)

    def get_skill(self, skill_id: str) -> dict[str, Any] | None:
        return next((dict(s) for s in self._platform["skills"] if s["id"] == skill_id), None)

    def get_plugin(self, plugin_id: str) -> dict[str, Any] | None:
        return next((dict(p) for p in self._platform["plugins"] if p["id"] == plugin_id), None)

    def get_workplace(self, workplace_id: str) -> dict[str, Any] | None:
        return next((dict(w) for w in self._platform["workplaces"] if w["id"] == workplace_id), None)

    def get_schedule(self, schedule_id: str) -> dict[str, Any] | None:
        return next((dict(s) for s in self._platform["schedules"] if s["id"] == schedule_id), None)

    def get_eval_run(self, run_id: str) -> dict[str, Any] | None:
        return next((dict(r) for r in self._platform["eval_runs"] if r["id"] == run_id), None)

    # -- agent-derived platform views ------------------------------------
    def get_agent_tools(self, agent_id: str) -> list[dict[str, Any]]:
        count = (self.get_agent(agent_id) or {}).get("tool_count", 0)
        return [dict(t, enabled=i < count) for i, t in enumerate(self.list_tools())]

    def get_agent_skills(self, agent_id: str) -> list[dict[str, Any]]:
        assigned = {"main": {"onboarding", "deploy"}, "ops": {"deploy"}, "research": {"research_brief"}}.get(agent_id, set())
        return [dict(s, assigned=s["id"] in assigned) for s in self.list_skills()]

    def get_agent_channels(self, agent_id: str) -> list[dict[str, Any]]:
        base = [
            {"id": "web", "type": "web", "name": "Web UI", "status": "active"},
            {"id": "telegram", "type": "telegram", "name": "Telegram", "status": "planned"},
            {"id": "whatsapp", "type": "whatsapp", "name": "WhatsApp", "status": "planned"},
        ]
        n = (self.get_agent(agent_id) or {}).get("channel_count", 1)
        return [dict(c, enabled=i < n) for i, c in enumerate(base)]


store = Store()
