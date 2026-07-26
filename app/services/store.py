"""Thin store facade: SQLite for agents/sessions/messages/settings/agent_tools/
workplaces/knowledge_entries and ``platform_data`` for the remaining platform
lists (skills, plugins, schedules, models, providers, safety rules, users,
shared channels, eval_*). Tool catalog is sourced from the JSON tool registry.

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
from app.models.mixins import knowledge_entries as knowledge_store
from app.models.mixins import messages as messages_store
from app.models.mixins import sessions as sessions_store
from app.models.mixins import llm_profiles as llm_profiles_store
from app.models.mixins import settings as settings_store
from app.models.mixins import tools as tools_store
from app.models.mixins import workplaces as workplaces_store
from app.models.mixins.busy import BusyState
from app.models.schema import migrate
from app.models.seed import seed_if_empty
from app.runtime.tools.registry import get_openai_tools, get_registry
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
    seed_users,
)
from app.workplaces.manager import connect as workplace_connect


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
            "skills": seed_skills(),
            "plugins": seed_plugins(),
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

    def get_coordinator(self) -> dict[str, Any] | None:
        """Enabled super agent, else first enabled agent (home-chat default)."""
        with self._lock:
            return agents_store.get_coordinator(self._conn, self._busy.ids())

    def create_home_session(self, user_id: str = "web") -> dict[str, Any]:
        """Create a coordinator-only session for the dashboard chat home.

        Returns ``session_id``, ``coordinator_id``, and ``coordinator_name``.
        Raises ``ValueError`` when no enabled coordinator/agent exists.
        """
        with self._lock:
            coord = agents_store.get_coordinator(self._conn, self._busy.ids())
            if not coord:
                raise ValueError("No enabled coordinator agent available")
            session_id = sessions_store.create_swarm_session(
                self._conn, [coord["id"]], user_id, coord["id"]
            )
            return {
                "session_id": session_id,
                "coordinator_id": coord["id"],
                "coordinator_name": coord["name"],
            }

    def create_agent(self, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            return agents_store.create_agent(self._conn, data)

    def update_agent(self, agent_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            if "workplace_id" in data and data["workplace_id"]:
                wid = str(data["workplace_id"]).strip()
                if wid and not workplaces_store.get_workplace(self._conn, wid):
                    raise ValueError(f"Workplace not found: {wid}")
                data = {**data, "workplace_id": wid}
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

    def set_session_title(self, session_id: str, title: str) -> dict[str, Any] | None:
        with self._lock:
            return sessions_store.set_session_title(self._conn, session_id, title)

    def get_or_create_session(self, agent_id: str, user_id: str) -> str:
        with self._lock:
            return sessions_store.get_or_create_session(self._conn, agent_id, user_id)

    # -- messages / history (SQLite) -------------------------------------
    def get_session_history(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return messages_store.get_session_history(self._conn, session_id)

    def append_session_history(self, session_id: str, entry: dict[str, Any]) -> str | None:
        """Append history. Returns new session title when auto-resolved from first user message."""
        with self._lock:
            return messages_store.append_session_history(self._conn, session_id, entry)

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
            sid = sessions_store.find_session(self._conn, agent_id, user_id)
            if sid is None:
                return
            messages_store.clear_session_history(self._conn, sid)

    # -- stats / dashboard -----------------------------------------------
    def _stats_from(
        self,
        agents: list[dict[str, Any]],
        sessions: list[dict[str, Any]],
        workplace_count: int,
    ) -> dict[str, Any]:
        """Compute the stats dict from already-locked snapshots (no DB/lock access)."""
        enabled = [a for a in agents if a["enabled"]]
        return {
            "agent_count": len(agents),
            "enabled_agent_count": len(enabled),
            "session_count": len(sessions),
            "tool_count": len(get_registry().names()) or sum(a["tool_count"] for a in agents),
            "channel_count": sum(a["channel_count"] for a in agents),
            "active_channel_count": sum(a["channel_count"] for a in enabled),
            "skill_count": len(self._platform["skills"]) or sum(a["skill_count"] for a in agents),
            "workplace_count": workplace_count,
        }

    def stats(self) -> dict[str, Any]:
        with self._lock:
            agents = agents_store.list_agents(self._conn, self._busy.ids())
            sessions = sessions_store.list_sessions(self._conn)
            wps = workplaces_store.list_workplaces(self._conn)
            return self._stats_from(agents, sessions, len(wps))

    def dashboard_data(self) -> dict[str, Any]:
        with self._lock:
            agents = agents_store.list_agents(self._conn, self._busy.ids())
            sessions = sessions_store.list_sessions(self._conn)
            workplaces = workplaces_store.list_workplaces(self._conn)
            recent_agents = sorted(agents, key=lambda a: a["created_at"], reverse=True)[:5]
            return {
                "stats": self._stats_from(agents, sessions, len(workplaces)),
                "recent_agents": recent_agents,
                "recent_sessions": sessions[:6],
                "busy_agents": [a["id"] for a in agents if a["busy"]],
                "workplaces": workplaces[:3],
                "schedules": [dict(s) for s in self._platform["schedules"][:3]],
            }

    # -- settings (SQLite) -----------------------------------------------
    def get_settings(self) -> dict[str, Any]:
        """Full settings including raw ``llm_api_key`` (runtime / internal)."""
        with self._lock:
            return settings_store.get_settings(self._conn)

    def get_public_settings(self) -> dict[str, Any]:
        """Settings safe for HTTP/HTML — API key masked."""
        with self._lock:
            return settings_store.public_settings(settings_store.get_settings(self._conn))

    def update_settings(self, data: dict[str, Any]) -> dict[str, Any]:
        """Upsert settings; returns public (masked) view."""
        with self._lock:
            return settings_store.update_settings(self._conn, data)

    # -- llm profiles (SQLite) -------------------------------------------
    def list_llm_profiles(self) -> list[dict[str, Any]]:
        with self._lock:
            return llm_profiles_store.list_profiles(self._conn)

    def get_llm_profile(self, profile_id: str) -> dict[str, Any] | None:
        with self._lock:
            return llm_profiles_store.get_public_profile(self._conn, profile_id)

    def create_llm_profile(self, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            return llm_profiles_store.create_profile(self._conn, data)

    def update_llm_profile(self, profile_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            return llm_profiles_store.update_profile(self._conn, profile_id, data)

    def delete_llm_profile(self, profile_id: str) -> bool:
        with self._lock:
            return llm_profiles_store.delete_profile(self._conn, profile_id)

    def set_default_llm_profile(self, profile_id: str) -> None:
        with self._lock:
            llm_profiles_store.set_default_model_id(self._conn, profile_id)

    def get_default_llm_profile_id(self) -> str:
        with self._lock:
            return llm_profiles_store.get_default_model_id(self._conn)

    def resolve_llm_profile(self, agent_id: str | None = None) -> dict[str, Any] | None:
        """Decrypt + resolve the runtime LLM profile for an agent (or default)."""
        with self._lock:
            return llm_profiles_store.resolve_profile(self._conn, agent_id)

    def setup_default_profile(
        self, *, base_url: str, api_key: str, model: str, name: str = "Default"
    ) -> dict[str, Any]:
        """Create the first ``default`` profile and mark it default (setup wizard)."""
        with self._lock:
            prof = llm_profiles_store.create_profile(
                self._conn,
                {
                    "id": "default",
                    "name": name,
                    "base_url": base_url,
                    "api_key": api_key,
                    "model": model,
                    "enabled": True,
                },
            )
            llm_profiles_store.set_default_model_id(self._conn, "default")
            return prof

    def is_setup_complete(self) -> bool:
        return bool(self.get_settings().get("setup_complete", True))

    # -- workplaces (SQLite) ---------------------------------------------
    def list_workplaces(self) -> list[dict[str, Any]]:
        with self._lock:
            return workplaces_store.list_workplaces(self._conn)

    def get_workplace(self, workplace_id: str) -> dict[str, Any] | None:
        with self._lock:
            return workplaces_store.get_workplace(self._conn, workplace_id)

    def create_workplace(self, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            return workplaces_store.create_workplace(self._conn, data)

    def update_workplace(self, workplace_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            return workplaces_store.update_workplace(self._conn, workplace_id, data)

    def delete_workplace(self, workplace_id: str) -> bool:
        with self._lock:
            return workplaces_store.delete_workplace(self._conn, workplace_id)

    def connect_workplace(self, workplace_id: str) -> dict[str, Any] | None:
        """Run Connect/test; persist status. Returns result dict or ``None`` if missing."""
        with self._lock:
            secrets = workplaces_store.get_workplace_secrets(self._conn, workplace_id)
            if not secrets:
                return None
            result = workplace_connect(secrets)
            workplaces_store.set_status(self._conn, workplace_id, result["status"])
            wp = workplaces_store.get_workplace(self._conn, workplace_id)
            return {
                "ok": result["ok"],
                "status": result["status"],
                "message": result["message"],
                "workplace": wp,
            }

    def resolve_agent_workplace_root(self, agent_id: str) -> str | None:
        """Local workplace ``root_path`` for ``agent_id``, or ``None`` (use work/)."""
        with self._lock:
            return workplaces_store.resolve_local_root(self._conn, agent_id)

    # -- knowledge entries (SQLite) --------------------------------------
    def list_knowledge_entries(self) -> list[dict[str, Any]]:
        with self._lock:
            return knowledge_store.list_entries(self._conn)

    def get_knowledge_entry(self, entry_id: str) -> dict[str, Any] | None:
        with self._lock:
            return knowledge_store.get_entry(self._conn, entry_id)

    def create_knowledge_entry(self, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            return knowledge_store.create_entry(self._conn, data)

    def update_knowledge_entry(
        self, entry_id: str, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        with self._lock:
            return knowledge_store.update_entry(self._conn, entry_id, data)

    def delete_knowledge_entry(self, entry_id: str) -> bool:
        with self._lock:
            return knowledge_store.delete_entry(self._conn, entry_id)

    def search_knowledge(
        self, query: str, *, limit: int = 5
    ) -> list[dict[str, Any]]:
        with self._lock:
            return knowledge_store.search_entries(self._conn, query, limit=limit)

    # -- platform lists (registry + platform_data) -----------------------
    def _plat(self, key: str) -> list[dict[str, Any]]:
        return [dict(x) for x in self._platform[key]]

    def list_tools(self) -> list[dict[str, Any]]:
        """Global tool catalog from the JSON registry (not platform_data seed)."""
        return get_registry().list_catalog()

    def list_skills(self) -> list[dict[str, Any]]:
        return self._plat("skills")

    def list_plugins(self) -> list[dict[str, Any]]:
        return self._plat("plugins")

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

    def get_schedule(self, schedule_id: str) -> dict[str, Any] | None:
        return next((dict(s) for s in self._platform["schedules"] if s["id"] == schedule_id), None)

    def get_eval_run(self, run_id: str) -> dict[str, Any] | None:
        return next((dict(r) for r in self._platform["eval_runs"] if r["id"] == run_id), None)

    # -- agent-derived platform views ------------------------------------
    def get_agent_tools(self, agent_id: str) -> list[dict[str, Any]]:
        """Registry tools with per-agent enablement from ``agent_tools``."""
        catalog = self.list_tools()
        with self._lock:
            return tools_store.list_for_agent(self._conn, agent_id, catalog)

    def set_agent_tools(
        self, agent_id: str, enabled: dict[str, bool]
    ) -> list[dict[str, Any]] | None:
        """Persist enable/disable map; returns updated tool list or None if agent missing."""
        if not self.get_agent(agent_id):
            return None
        known = [t["id"] for t in self.list_tools()]
        with self._lock:
            tools_store.set_for_agent(self._conn, agent_id, enabled, known)
            return tools_store.list_for_agent(self._conn, agent_id, self.list_tools())

    def get_enabled_tool_ids(self, agent_id: str) -> set[str]:
        """Tool names advertised to the LLM for ``agent_id``."""
        known = get_registry().names()
        with self._lock:
            return tools_store.enabled_ids(self._conn, agent_id, known)

    def get_agent_openai_tools(self, agent_id: str) -> list[dict[str, Any]]:
        """OpenAI schemas filtered to tools enabled for ``agent_id``."""
        return get_openai_tools(self.get_enabled_tool_ids(agent_id))

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
