"""Thin store facade: SQLite for agents/sessions/messages/settings/agent_tools/
workplaces/knowledge_entries/skills/plugins/schedules/users and ``platform_data``
for remaining stub lists (models, providers, safety rules, channel users, shared
channels, eval_*). Tool catalog is sourced from the JSON tool registry.

Public method names match the previous JSON-backed store so the API/UI layer
is unchanged. Busy state is process-local and **session-scoped**
(:class:`app.models.mixins.busy.BusyState`) — never persisted to SQLite and
never shown as a global agent flag across chats.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from app.core.config import ADMIN_PASSWORD, DB_PATH
from app.models.db import get_connection
from app.models.mixins import agents as agents_store
from app.models.mixins import attachments as attachments_store
from app.models.mixins import knowledge_entries as knowledge_store
from app.models.mixins import messages as messages_store
from app.models.mixins import modules as modules_store
from app.models.mixins import schedules as schedules_store
from app.models.mixins import sessions as sessions_store
from app.models.mixins import skills as skills_store
from app.models.mixins import llm_profiles as llm_profiles_store
from app.models.mixins import settings as settings_store
from app.models.mixins import tools as tools_store
from app.models.mixins import users as users_store
from app.models.mixins import api_keys as api_keys_store
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
    seed_providers,
    seed_safety_rules,
    seed_shared_channels,
    seed_users,
)
from app.workplaces.manager import connect as workplace_connect


class Store:
    """Hybrid facade: SQLite for Alpha entities, platform_data for stubs."""

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
        users_store.ensure_bootstrap_admin(self._conn, ADMIN_PASSWORD)
        try:
            from modules.registry import sync_module_rows

            sync_module_rows(self._conn)
        except Exception:
            pass

    def with_db(self, fn):
        """Run ``fn(conn)`` under the store lock (for module ledger access)."""
        with self._lock:
            return fn(self._conn)

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
        """In-memory stubs still used for gated/eval and unused catalog tiles."""
        return {
            "providers": seed_providers(),
            "models": seed_models(),
            "safety_rules": seed_safety_rules(),
            "channel_users": seed_users(),
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

    def list_enabled_agent_ids(self) -> list[str]:
        """Ids of enabled agents (coordinator / super first)."""
        with self._lock:
            return agents_store.list_enabled_agent_ids(self._conn)

    def create_home_session(self, user_id: str = "web") -> dict[str, Any]:
        """Create a **full-swarm** session for the dashboard chat home.

        All enabled agents are members so delegate works without picking a team.
        Coordinator is the super agent (or first enabled). Raises ``ValueError``
        when no enabled agent exists.
        """
        with self._lock:
            coord = agents_store.get_coordinator(self._conn, self._busy.ids())
            if not coord:
                raise ValueError("No enabled coordinator agent available")
            swarm = agents_store.list_enabled_agent_ids(self._conn)
            session_id = sessions_store.create_swarm_session(
                self._conn, swarm, user_id, coord["id"]
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
                if wid and wid not in ("__all_tunnels__", "__all__"):
                    if not workplaces_store.get_workplace(self._conn, wid):
                        raise ValueError(f"Workplace not found: {wid}")
                data = {**data, "workplace_id": wid}
            if "workplace_ids" in data and data["workplace_ids"] is not None:
                ids = [str(x).strip() for x in data["workplace_ids"] if str(x).strip()]
                for wid in ids:
                    if not workplaces_store.get_workplace(self._conn, wid):
                        raise ValueError(f"Workplace not found: {wid}")
                data = {**data, "workplace_ids": ids}
            prev = agents_store.get_agent(self._conn, agent_id, self._busy.ids())
            agent = agents_store.update_agent(self._conn, agent_id, data, self._busy.ids())
        if (
            agent
            and prev
            and "artifacts_enabled" in data
            and bool(prev.get("artifacts_enabled", True))
            != bool(agent.get("artifacts_enabled", True))
        ):
            self.sync_artifact_tools(agent_id)
            agent = self.get_agent(agent_id)
        return agent

    def delete_agent(self, agent_id: str) -> bool:
        with self._lock:
            ok = agents_store.delete_agent(self._conn, agent_id)
            if ok:
                self._busy.clear_agent(agent_id)
            return ok

    def set_busy(self, agent_id: str, busy: bool, *, session_id: str) -> None:
        """Mark agent busy for ``session_id`` only (not cross-session UI)."""
        with self._lock:
            self._busy.set_busy(agent_id, busy, session_id=session_id)

    def is_agent_busy(self, agent_id: str, session_id: str) -> bool:
        with self._lock:
            return self._busy.is_busy(agent_id, session_id)

    def try_begin_session_turn(self, session_id: str) -> bool:
        """Exclusive session turn lock (prevents concurrent SSE turns)."""
        with self._lock:
            return self._busy.try_begin_session_turn(session_id)

    def end_session_turn(self, session_id: str) -> None:
        with self._lock:
            self._busy.end_session_turn(session_id)

    def is_session_turn_active(self, session_id: str) -> bool:
        """True while the session-turn lease is held (background or direct stream)."""
        with self._lock:
            return self._busy.is_session_turn_active(session_id)

    # -- sessions (SQLite) -----------------------------------------------
    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            # Keep swarm membership current with enabled agents.
            try:
                sessions_store.sync_swarm_membership(self._conn, session_id)
            except Exception:
                pass
            return sessions_store.get_session(self._conn, session_id)

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = sessions_store.list_sessions(self._conn)
            # Soft-sync swarm membership so new agents appear without a full turn.
            for s in rows:
                try:
                    sessions_store.sync_swarm_membership(self._conn, s["id"])
                except Exception:
                    pass
            return sessions_store.list_sessions(self._conn)

    def create_swarm_session(
        self,
        agent_ids: list[str],
        user_id: str = "web",
        coordinator_id: str | None = None,
        workplace_id: str | None = None,
    ) -> str:
        with self._lock:
            return sessions_store.create_swarm_session(
                self._conn,
                agent_ids,
                user_id,
                coordinator_id,
                workplace_id=workplace_id,
            )

    def set_session_workplace(
        self, session_id: str, workplace_id: str | None
    ) -> dict[str, Any] | None:
        with self._lock:
            return sessions_store.set_session_workplace(
                self._conn, session_id, workplace_id
            )

    def update_session_agents(self, session_id: str, agent_ids: list[str]) -> dict[str, Any] | None:
        with self._lock:
            return sessions_store.update_session_agents(self._conn, session_id, agent_ids)

    def set_session_title(self, session_id: str, title: str) -> dict[str, Any] | None:
        with self._lock:
            return sessions_store.set_session_title(self._conn, session_id, title)

    def get_or_create_session(self, agent_id: str, user_id: str) -> str:
        with self._lock:
            return sessions_store.get_or_create_session(self._conn, agent_id, user_id)

    def find_session(self, agent_id: str, user_id: str) -> str | None:
        with self._lock:
            return sessions_store.find_session(self._conn, agent_id, user_id)

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            return sessions_store.delete_session(self._conn, session_id)

    def prune_empty_draft_sessions(self, *, keep_id: str | None = None) -> list[str]:
        with self._lock:
            return sessions_store.prune_empty_draft_sessions(self._conn, keep_id=keep_id)

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
        try:
            from app.runtime.memory.curated import reset_freeze

            reset_freeze(session_id=session_id)
        except Exception:
            pass

    # -- attachments (SQLite) --------------------------------------------
    def create_attachment(
        self,
        attachment_id: str,
        session_id: str,
        filename: str,
        original_name: str,
        mime_type: str,
        size_bytes: int,
        file_path: str,
    ) -> dict[str, Any]:
        with self._lock:
            return attachments_store.create_attachment(
                self._conn, attachment_id, session_id, filename, original_name,
                mime_type, size_bytes, file_path,
            )

    def get_attachment(self, attachment_id: str) -> dict[str, Any] | None:
        with self._lock:
            return attachments_store.get_attachment(self._conn, attachment_id)

    def list_session_attachments(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return attachments_store.list_session_attachments(self._conn, session_id)

    def delete_attachment(self, attachment_id: str) -> bool:
        with self._lock:
            return attachments_store.delete_attachment(self._conn, attachment_id)

    def search_messages(
        self, query: str, *, limit: int = 10
    ) -> list[dict[str, Any]]:
        with self._lock:
            return messages_store.search_messages(self._conn, query, limit=limit)

    def append_history(self, agent_id: str, user_id: str, entry: dict[str, Any]) -> None:
        with self._lock:
            sid = sessions_store.get_or_create_session(self._conn, agent_id, user_id)
            messages_store.append_session_history(self._conn, sid, entry)

    def get_history(self, agent_id: str, user_id: str) -> list[dict[str, Any]]:
        with self._lock:
            sid = sessions_store.get_or_create_session(self._conn, agent_id, user_id)
            return messages_store.get_session_history(self._conn, sid)

    def clear_session(self, agent_id: str, user_id: str) -> None:
        sid = None
        with self._lock:
            sid = sessions_store.find_session(self._conn, agent_id, user_id)
            if sid is None:
                return
            messages_store.clear_session_history(self._conn, sid)
        try:
            from app.runtime.memory.curated import reset_freeze

            reset_freeze(session_id=sid)
        except Exception:
            pass

    # -- stats / dashboard -----------------------------------------------
    def _stats_from(
        self,
        agents: list[dict[str, Any]],
        sessions: list[dict[str, Any]],
        workplace_count: int,
        skill_count: int,
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
            "skill_count": skill_count or sum(a["skill_count"] for a in agents),
            "workplace_count": workplace_count,
        }

    def stats(self) -> dict[str, Any]:
        with self._lock:
            agents = agents_store.list_agents(self._conn, self._busy.ids())
            sessions = sessions_store.list_sessions(self._conn)
            wps = workplaces_store.list_workplaces(self._conn)
            skills = skills_store.list_skills(self._conn)
            return self._stats_from(agents, sessions, len(wps), len(skills))

    def dashboard_data(self) -> dict[str, Any]:
        with self._lock:
            agents = agents_store.list_agents(self._conn, self._busy.ids())
            sessions = sessions_store.list_sessions(self._conn)
            workplaces = workplaces_store.list_workplaces(self._conn)
            skills = skills_store.list_skills(self._conn)
            schedules = schedules_store.list_schedules(self._conn)
            recent_agents = sorted(agents, key=lambda a: a["created_at"], reverse=True)[:5]
            return {
                "stats": self._stats_from(agents, sessions, len(workplaces), len(skills)),
                "recent_agents": recent_agents,
                "recent_sessions": sessions[:6],
                "busy_agents": [a["id"] for a in agents if a["busy"]],
                "workplaces": workplaces[:3],
                "schedules": schedules[:3],
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

    def get_workplace_secrets(self, workplace_id: str) -> dict[str, Any] | None:
        """Runtime view with decrypted SSH secrets (never expose via public API)."""
        with self._lock:
            return workplaces_store.get_workplace_secrets(self._conn, workplace_id)

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
            # Merge public flags (pairing_code, token_set) for tunnel messaging.
            public = workplaces_store.get_workplace(self._conn, workplace_id) or {}
            view = {**secrets, **{
                k: public.get(k)
                for k in (
                    "pairing_code",
                    "connector_token_set",
                    "status",
                )
            }}
            result = workplace_connect(view)
            allow = result["status"] == "connected" and secrets.get("kind") == "tunnel"
            workplaces_store.set_status(
                self._conn,
                workplace_id,
                result["status"],
                allow_connected=bool(allow),
            )
            wp = workplaces_store.get_workplace(self._conn, workplace_id)
            return {
                "ok": result["ok"],
                "status": result["status"],
                "message": result["message"],
                "workplace": wp,
            }

    def issue_pairing_code(self, workplace_id: str) -> dict[str, Any] | None:
        with self._lock:
            return workplaces_store.issue_pairing_code(self._conn, workplace_id)

    def pair_connector(
        self,
        code: str,
        *,
        hostname: str = "",
        version: str = "",
        platform: str = "",
        remote_ip: str = "",
    ) -> dict[str, Any] | None:
        """Consume pairing code; return workplace_id + plaintext token."""
        with self._lock:
            wp = workplaces_store.find_by_pairing_code(self._conn, code)
            if not wp:
                return None
            token = workplaces_store.complete_pairing(
                self._conn,
                wp["id"],
                hostname=hostname,
                version=version,
                platform=platform,
                remote_ip=remote_ip,
                rotate_token=True,
            )
            return {
                "workplace_id": wp["id"],
                "workplace_name": wp.get("name") or wp["id"],
                "token": token,
            }

    def hello_connector(
        self,
        token: str,
        *,
        hostname: str = "",
        version: str = "",
        platform: str = "",
        remote_ip: str = "",
    ) -> dict[str, Any] | None:
        """Authenticate with long-lived token; mark connected in DB."""
        with self._lock:
            wp = workplaces_store.find_by_connector_token(self._conn, token)
            if not wp:
                return None
            workplaces_store.mark_connector_seen(
                self._conn,
                wp["id"],
                hostname=hostname,
                version=version,
                platform=platform,
                remote_ip=remote_ip,
                status="connected",
            )
            return {"workplace_id": wp["id"]}

    def touch_connector(
        self,
        workplace_id: str,
        *,
        remote_ip: str = "",
        hostname: str = "",
        version: str = "",
        platform: str = "",
    ) -> None:
        with self._lock:
            workplaces_store.mark_connector_seen(
                self._conn,
                workplace_id,
                hostname=hostname,
                version=version,
                platform=platform,
                remote_ip=remote_ip,
                status="connected",
            )

    def mark_connector_offline(self, workplace_id: str) -> None:
        with self._lock:
            workplaces_store.mark_connector_offline(self._conn, workplace_id)

    def resolve_agent_workplace_root(self, agent_id: str) -> str | None:
        """Local workplace ``root_path`` for ``agent_id``, or ``None`` (use work/)."""
        with self._lock:
            return workplaces_store.resolve_local_root(self._conn, agent_id)

    def resolve_agent_workplace(self, agent_id: str) -> dict[str, Any] | None:
        """Assigned workplace (public) for ``agent_id``, or ``None``."""
        with self._lock:
            return workplaces_store.resolve_agent_workplace(self._conn, agent_id)

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

    # -- skills / plugins / schedules (SQLite) ---------------------------
    def list_skills(self) -> list[dict[str, Any]]:
        with self._lock:
            return skills_store.list_skills(self._conn)

    def get_skill(self, skill_id: str) -> dict[str, Any] | None:
        with self._lock:
            return skills_store.get_skill(self._conn, skill_id)

    def update_skill(self, skill_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            return skills_store.update_skill(self._conn, skill_id, data)

    def delete_skill(self, skill_id: str) -> bool:
        with self._lock:
            return skills_store.delete_skill(self._conn, skill_id)

    def sync_skills(self) -> list[dict[str, Any]]:
        """Rescan library + external skill dirs into SQLite."""
        from app.extensions.skills import sync_skills_to_db

        with self._lock:
            return sync_skills_to_db(self._conn)

    def install_skill_from_path(self, path: str | Path, skill_id: str | None = None) -> dict[str, Any]:
        from app.extensions.skills import install_from_path, sync_skills_to_db

        installed = install_from_path(Path(path), skill_id=skill_id)
        with self._lock:
            sync_skills_to_db(self._conn)
            skill = skills_store.get_skill(self._conn, installed.id)
        if skill is None:
            raise RuntimeError(f"skill {installed.id} not found after install")
        return skill

    def uninstall_library_skill(self, skill_id: str) -> bool:
        from app.extensions.skills import uninstall_library_skill

        removed = uninstall_library_skill(skill_id)
        with self._lock:
            if removed:
                skills_store.delete_skill(self._conn, skill_id)
            else:
                # Still drop DB row if it was a synced external entry user wants gone from catalog
                # — only library uninstall removes files; for catalog-only use delete_skill.
                pass
        return removed

    def list_plugins(self) -> list[dict[str, Any]]:
        """Deprecated alias for :meth:`list_modules`."""
        return self.list_modules()

    def list_modules(self) -> list[dict[str, Any]]:
        with self._lock:
            return modules_store.list_modules(self._conn)

    def enabled_module_ids(self) -> set[str]:
        with self._lock:
            return {
                m["id"]
                for m in modules_store.list_modules(self._conn)
                if m.get("enabled")
            }

    def get_plugin(self, plugin_id: str) -> dict[str, Any] | None:
        return self.get_module(plugin_id)

    def get_module(self, module_id: str) -> dict[str, Any] | None:
        with self._lock:
            return modules_store.get_module(self._conn, module_id)

    def update_plugin(self, plugin_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        return self.update_module(plugin_id, data)

    def update_module(self, module_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            return modules_store.update_module(self._conn, module_id, data)

    def is_plugin_enabled(self, plugin_id: str) -> bool:
        return self.is_module_enabled(plugin_id)

    def is_module_enabled(self, module_id: str) -> bool:
        with self._lock:
            return modules_store.is_module_enabled(self._conn, module_id)

    def dispatch_turn_end(
        self,
        *,
        session_id: str,
        agent_id: str,
        message: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        """Notify enabled modules that a session turn finished."""
        from modules.base import TurnEndContext
        from modules.registry import on_turn_end

        with self._lock:
            on_turn_end(
                self._conn,
                TurnEndContext(
                    session_id=session_id,
                    agent_id=agent_id,
                    message=message,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                ),
            )

    def list_schedules(self) -> list[dict[str, Any]]:
        with self._lock:
            return schedules_store.list_schedules(self._conn)

    def get_schedule(self, schedule_id: str) -> dict[str, Any] | None:
        with self._lock:
            return schedules_store.get_schedule(self._conn, schedule_id)

    def create_schedule(self, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            return schedules_store.create_schedule(self._conn, data)

    def update_schedule(
        self, schedule_id: str, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        with self._lock:
            return schedules_store.update_schedule(self._conn, schedule_id, data)

    def delete_schedule(self, schedule_id: str) -> bool:
        with self._lock:
            return schedules_store.delete_schedule(self._conn, schedule_id)

    def list_due_schedules(self, now: float | None = None) -> list[dict[str, Any]]:
        with self._lock:
            return schedules_store.list_due(self._conn, now)

    def begin_schedule_run(
        self,
        schedule_id: str,
        *,
        session_id: str | None = None,
        now: float | None = None,
    ) -> str:
        with self._lock:
            return schedules_store.begin_run(
                self._conn, schedule_id, session_id=session_id, now=now
            )

    def finish_schedule_run(
        self,
        run_id: str,
        *,
        status: str = "ok",
        error: str = "",
        session_id: str | None = None,
        now: float | None = None,
    ) -> None:
        with self._lock:
            schedules_store.finish_run(
                self._conn,
                run_id,
                status=status,
                error=error,
                session_id=session_id,
                now=now,
            )

    def list_schedule_runs(
        self, schedule_id: str | None = None, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        with self._lock:
            return schedules_store.list_runs(self._conn, schedule_id, limit=limit)

    # -- platform stubs (platform_data) ----------------------------------
    def _plat(self, key: str) -> list[dict[str, Any]]:
        return [dict(x) for x in self._platform[key]]

    def list_tools(self) -> list[dict[str, Any]]:
        """Global tool catalog from the JSON registry (not platform_data seed)."""
        return get_registry().list_catalog()

    def list_models(self) -> list[dict[str, Any]]:
        return self._plat("models")

    def list_providers(self) -> list[dict[str, Any]]:
        return self._plat("providers")

    def list_safety_rules(self) -> list[dict[str, Any]]:
        return self._plat("safety_rules")

    # -- login accounts (SQLite) -----------------------------------------
    def list_users(self) -> list[dict[str, Any]]:
        with self._lock:
            return users_store.list_users(self._conn)

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self._lock:
            return users_store.get_user(self._conn, user_id)

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        with self._lock:
            return users_store.get_user_by_username(self._conn, username)

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        with self._lock:
            return users_store.authenticate(self._conn, username, password)

    def create_user(self, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            return users_store.create_user(self._conn, data)

    def update_user(self, user_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            return users_store.update_user(self._conn, user_id, data)

    def delete_user(self, user_id: str) -> bool:
        with self._lock:
            return users_store.delete_user(self._conn, user_id)

    def count_enabled_users(self) -> int:
        with self._lock:
            return users_store.count_enabled(self._conn)

    # -- API keys (SQLite) -----------------------------------------------
    def list_api_keys(self, user_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            return api_keys_store.list_api_keys(self._conn, user_id)

    def get_api_key(self, key_id: str) -> dict[str, Any] | None:
        with self._lock:
            return api_keys_store.get_api_key(self._conn, key_id)

    def create_api_key(self, user_id: str, name: str = "") -> dict[str, Any]:
        with self._lock:
            return api_keys_store.create_api_key(self._conn, user_id, name)

    def delete_api_key(self, key_id: str) -> bool:
        with self._lock:
            return api_keys_store.delete_api_key(self._conn, key_id)

    def authenticate_api_key(self, token: str) -> dict[str, Any] | None:
        with self._lock:
            return api_keys_store.authenticate_api_key(self._conn, token)

    def list_channel_users(self) -> list[dict[str, Any]]:
        """In-memory channel allowlist stub (not login accounts)."""
        return self._plat("channel_users")

    def list_shared_channels(self) -> list[dict[str, Any]]:
        from app.channels.telegram import telegram_status

        status = telegram_status(self.get_settings())
        out: list[dict[str, Any]] = []
        for ch in self._plat("shared_channels"):
            row = dict(ch)
            if row.get("type") == "telegram":
                row["status"] = status
            out.append(row)
        return out

    def list_eval_domains(self) -> list[dict[str, Any]]:
        return self._plat("eval_domains")

    def list_evaluators(self) -> list[dict[str, Any]]:
        return self._plat("evaluators")

    def list_eval_runs(self) -> list[dict[str, Any]]:
        return sorted(self._plat("eval_runs"), key=lambda r: r.get("started_at", 0), reverse=True)

    def get_eval_run(self, run_id: str) -> dict[str, Any] | None:
        return next((dict(r) for r in self._platform["eval_runs"] if r["id"] == run_id), None)

    # -- agent-derived platform views ------------------------------------
    def get_agent_tools(self, agent_id: str) -> list[dict[str, Any]]:
        """Registry tools with per-agent enablement from ``agent_tools``."""
        from app.runtime.artifacts.fs import ARTIFACT_TOOLS

        catalog = self.list_tools()
        with self._lock:
            rows = tools_store.list_for_agent(self._conn, agent_id, catalog)
        agent = self.get_agent(agent_id)
        arts_on = bool(agent.get("artifacts_enabled", True)) if agent else True
        out: list[dict[str, Any]] = []
        for t in rows:
            row = dict(t)
            if t["id"] in ARTIFACT_TOOLS:
                row["locked"] = True
                row["enabled"] = arts_on
            out.append(row)
        return out

    def set_agent_tools(
        self, agent_id: str, enabled: dict[str, bool]
    ) -> list[dict[str, Any]] | None:
        """Persist enable/disable map; returns updated tool list or None if agent missing."""
        agent = self.get_agent(agent_id)
        if not agent:
            return None
        from app.runtime.artifacts.fs import ARTIFACT_TOOLS

        # Artifact tools are locked to artifacts_enabled — callers cannot override.
        locked = dict(enabled)
        if agent.get("artifacts_enabled", True):
            for tid in ARTIFACT_TOOLS:
                locked[tid] = True
        else:
            for tid in ARTIFACT_TOOLS:
                locked[tid] = False
        known = [t["id"] for t in self.list_tools()]
        with self._lock:
            tools_store.set_for_agent(self._conn, agent_id, locked, known)
            return tools_store.list_for_agent(self._conn, agent_id, self.list_tools())

    def get_enabled_tool_ids(self, agent_id: str) -> set[str]:
        """Tool names advertised to the LLM for ``agent_id``."""
        known = get_registry().names()
        with self._lock:
            ids = set(tools_store.enabled_ids(self._conn, agent_id, known))
        agent = self.get_agent(agent_id)
        from app.runtime.artifacts.fs import ARTIFACT_TOOLS

        if agent and not agent.get("artifacts_enabled", True):
            ids -= ARTIFACT_TOOLS
        elif agent and agent.get("artifacts_enabled", True):
            ids |= ARTIFACT_TOOLS & set(known)
        return ids

    def sync_artifact_tools(self, agent_id: str) -> None:
        """Re-apply artifact tool lock after ``artifacts_enabled`` flips."""
        agent = self.get_agent(agent_id)
        if not agent:
            return
        catalog = self.list_tools()
        with self._lock:
            rows = tools_store.list_for_agent(self._conn, agent_id, catalog)
        enabled = {t["id"]: bool(t.get("enabled")) for t in rows}
        self.set_agent_tools(agent_id, enabled)

    def get_agent_openai_tools(self, agent_id: str) -> list[dict[str, Any]]:
        """OpenAI schemas filtered to tools enabled for ``agent_id``."""
        return get_openai_tools(self.get_enabled_tool_ids(agent_id))

    def get_agent_skills(self, agent_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return skills_store.list_for_agent(self._conn, agent_id)

    def set_agent_skills(
        self, agent_id: str, skill_ids: list[str]
    ) -> list[dict[str, Any]] | None:
        if not self.get_agent(agent_id):
            return None
        with self._lock:
            return skills_store.set_for_agent(self._conn, agent_id, skill_ids)

    def get_agent_channels(self, agent_id: str) -> list[dict[str, Any]]:
        from app.channels.telegram import telegram_status

        tg = telegram_status(self.get_settings())
        base = [
            {"id": "web", "type": "web", "name": "Web UI", "status": "active"},
            {"id": "telegram", "type": "telegram", "name": "Telegram", "status": tg},
            {"id": "whatsapp", "type": "whatsapp", "name": "WhatsApp", "status": "planned"},
        ]
        n = (self.get_agent(agent_id) or {}).get("channel_count", 1)
        return [dict(c, enabled=i < n) for i, c in enumerate(base)]

    # -- memory layers (state / artifacts / session summaries) ----------
    def list_agent_state(self, agent_id: str) -> dict[str, str]:
        from app.runtime.memory import layers as mem_layers

        with self._lock:
            return mem_layers.list_agent_state(self._conn, agent_id)

    def get_agent_state_value(self, agent_id: str, key: str) -> str | None:
        from app.runtime.memory import layers as mem_layers

        with self._lock:
            return mem_layers.get_agent_state(self._conn, agent_id, key)

    def set_agent_state_value(self, agent_id: str, key: str, value: str) -> None:
        from app.runtime.memory import layers as mem_layers

        with self._lock:
            mem_layers.set_agent_state(self._conn, agent_id, key, value)

    def delete_agent_state_value(self, agent_id: str, key: str) -> bool:
        from app.runtime.memory import layers as mem_layers

        with self._lock:
            return mem_layers.delete_agent_state(self._conn, agent_id, key)

    def create_artifact(self, data: dict[str, Any]) -> dict[str, Any]:
        from app.runtime.memory import layers as mem_layers

        with self._lock:
            return mem_layers.create_artifact(self._conn, data)

    def search_artifacts(
        self, query: str, *, limit: int = 5, session_id: str | None = None
    ) -> list[dict[str, Any]]:
        from app.runtime.memory import layers as mem_layers

        with self._lock:
            return mem_layers.search_artifacts(
                self._conn, query, limit=limit, session_id=session_id
            )

    def list_artifacts(self, *, limit: int = 20) -> list[dict[str, Any]]:
        from app.runtime.memory import layers as mem_layers

        with self._lock:
            return mem_layers.list_artifacts(self._conn, limit=limit)

    def get_session_summary(self, session_id: str) -> dict[str, Any] | None:
        from app.runtime.memory import layers as mem_layers

        with self._lock:
            return mem_layers.get_session_summary(self._conn, session_id)

    def upsert_session_summary(
        self, session_id: str, summary: str, *, message_count: int = 0
    ) -> dict[str, Any]:
        from app.runtime.memory import layers as mem_layers

        with self._lock:
            return mem_layers.upsert_session_summary(
                self._conn, session_id, summary, message_count=message_count
            )

    def bump_skill_use(self, skill_id: str) -> None:
        with self._lock:
            cols = {r[1] for r in self._conn.execute("PRAGMA table_info(skills)")}
            if "use_count" not in cols:
                return
            self._conn.execute(
                "UPDATE skills SET use_count=COALESCE(use_count,0)+1, "
                "last_used_at=? WHERE id=?",
                (time.time(), skill_id),
            )
            self._conn.commit()


store = Store()
