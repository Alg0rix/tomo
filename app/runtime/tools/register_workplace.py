"""register_workplace — create a workplace and optionally bind it for this turn."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.runtime.tools.sandbox import current_agent_id
from app.runtime.tools.workplace_ctx import bind_workplace


def run(arguments: dict[str, Any]) -> str:
    if not isinstance(arguments, dict):
        return "Error: register_workplace expects a dict of arguments"

    kind = str(arguments.get("kind") or "local").strip().lower()
    if kind not in ("local", "ssh", "tunnel"):
        return "Error: kind must be local, ssh, or tunnel"

    name = str(arguments.get("name") or "").strip()
    path = str(
        arguments.get("path") or arguments.get("root_path") or ""
    ).strip()
    assign = arguments.get("assign_to_agent")
    if assign is None:
        assign = True
    bind = arguments.get("use_now")
    if bind is None:
        bind = True

    if kind == "local":
        if not path:
            return "Error: local workplace requires 'path' (root directory)"
        p = Path(path).expanduser()
        try:
            p = p.resolve()
        except OSError as exc:
            return f"Error: invalid path: {exc}"
        if not p.is_dir():
            return f"Error: path is not a directory: {p}"
        path = str(p)
        if not name:
            name = p.name or "local"

    if not name:
        name = kind

    data: dict[str, Any] = {"name": name, "kind": kind}
    if kind == "local":
        data["root_path"] = path
    if kind == "ssh":
        data["ssh_host"] = str(arguments.get("ssh_host") or arguments.get("host") or "")
        data["ssh_user"] = str(arguments.get("ssh_user") or arguments.get("user") or "")
        data["ssh_port"] = int(arguments.get("ssh_port") or 22)
        data["ssh_password"] = str(arguments.get("ssh_password") or "")
        data["ssh_key"] = str(arguments.get("ssh_key") or "")
        data["root_path"] = path
        if not data["ssh_host"] or not data["ssh_user"]:
            return "Error: ssh workplace needs ssh_host and ssh_user"

    try:
        from app.services import store

        # Reuse existing local workplace with same root_path.
        if kind == "local":
            for w in store.list_workplaces():
                if (w.get("kind") == "local" and (w.get("root_path") or "") == path):
                    wp = w
                    created = False
                    break
            else:
                wp = store.create_workplace(data)
                created = True
        else:
            wp = store.create_workplace(data)
            created = True

        aid = current_agent_id()
        if assign and aid:
            agent = store.get_agent(aid)
            if agent:
                scope = agent.get("workplace_scope") or "single"
                ids = list(agent.get("workplace_ids") or [])
                wid = wp["id"]
                kind_wp = (wp.get("kind") or "").strip().lower()
                # all already covers every workplace; all_tunnels covers tunnels only.
                covered = scope == "all" or (
                    scope == "all_tunnels" and kind_wp == "tunnel"
                )
                if not covered:
                    if scope == "single" and not agent.get("workplace_id"):
                        store.update_agent(
                            aid,
                            {
                                "workplace_id": wid,
                                "workplace_ids": [wid],
                                "workplace_scope": "single",
                            },
                        )
                    else:
                        # Expand allowlist (including all_tunnels + new local).
                        if agent.get("workplace_id") and agent["workplace_id"] not in ids:
                            ids.append(agent["workplace_id"])
                        if wid not in ids:
                            ids.append(wid)
                        # If was all_tunnels and we add a non-tunnel, become list.
                        new_scope = "list" if len(ids) > 1 or scope == "all_tunnels" else "single"
                        if scope == "all_tunnels" and kind_wp != "tunnel":
                            new_scope = "list"
                        store.update_agent(
                            aid,
                            {
                                "workplace_id": agent.get("workplace_id") or wid,
                                "workplace_ids": ids,
                                "workplace_scope": new_scope,
                            },
                        )

        if bind:
            bind_workplace(workplace_id=wp["id"])

        verb = "Registered" if created else "Reused"
        return (
            f"{verb} workplace {wp['id']!r} ({wp.get('kind')}) "
            f"name={wp.get('name')!r} "
            f"path={wp.get('root_path') or wp.get('host') or '—'}"
        )
    except Exception as exc:
        return f"Error: could not register workplace: {exc}"


__all__ = ["run"]
