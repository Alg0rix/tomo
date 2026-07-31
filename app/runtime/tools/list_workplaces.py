"""list_workplaces — catalog Tomo workplaces from the store (not the filesystem)."""

from __future__ import annotations

from typing import Any

from app.runtime.tools.sandbox import current_agent_id
from app.runtime.tools.workplace_remote import _agent_allowed_workplaces


def _line(w: dict[str, Any]) -> str:
    kind = (w.get("kind") or "?").strip().lower()
    name = w.get("name") or w.get("id") or "?"
    wid = w.get("id") or "?"
    parts = [f"{name}", f"id={wid}", f"kind={kind}"]

    if kind == "tunnel":
        online = w.get("online")
        if online is True:
            parts.append("online")
        elif online is False:
            parts.append("offline")
        host = (w.get("connector_hostname") or w.get("host") or "").strip()
        if " (" in host and host.endswith(")"):
            host = host.split(" (", 1)[0].strip()
        ip = (w.get("connector_remote_ip") or "").strip()
        if host:
            parts.append(f"hostname={host}")
        if ip and ip not in ("127.0.0.1", "::1"):
            parts.append(f"ip={ip}")
        ver = (w.get("connector_version") or "").strip()
        if ver:
            parts.append(f"v={ver}")
    elif kind == "ssh":
        user = (w.get("ssh_user") or "").strip()
        host = (w.get("host") or w.get("ssh_host") or "").strip()
        if user and host:
            parts.append(f"{user}@{host}")
        elif host:
            parts.append(f"host={host}")
        online = w.get("online")
        if online is True:
            parts.append("reachable")
        elif online is False:
            parts.append("unreachable")
    elif kind == "local":
        root = (w.get("root_path") or w.get("path") or "").strip()
        if root:
            parts.append(f"path={root}")
        st = (w.get("status") or "").strip()
        if st:
            parts.append(st)

    return " · ".join(parts)


def _catalog_for_agent(agent: dict[str, Any]) -> list[dict[str, Any]]:
    from app.services import store

    # Coordinators need the full map to route; specialists see allowed only.
    if agent.get("is_super"):
        return list(store.list_workplaces())
    scope = (agent.get("workplace_scope") or "single").strip().lower()
    if scope == "all":
        return list(store.list_workplaces())
    return _agent_allowed_workplaces(agent)


def run(arguments: dict[str, Any] | None = None) -> str:
    if arguments is not None and not isinstance(arguments, dict):
        return "Error: list_workplaces expects a dict of arguments"
    args = arguments or {}

    aid = current_agent_id()
    from app.services import store

    if not aid:
        wps = list(store.list_workplaces())
        agent: dict[str, Any] = {"is_super": True, "workplace_scope": "all"}
    else:
        agent = store.get_agent(aid) or {}
        wps = _catalog_for_agent(agent)

    kind = str(args.get("kind") or "all").strip().lower()
    if kind and kind != "all":
        wps = [w for w in wps if (w.get("kind") or "").strip().lower() == kind]

    online_only = bool(args.get("online_only"))
    if online_only:
        filtered: list[dict[str, Any]] = []
        for w in wps:
            k = (w.get("kind") or "").strip().lower()
            if k in ("tunnel", "ssh"):
                if w.get("online") is True:
                    filtered.append(w)
            else:
                # local: keep if path exists / ready
                if w.get("online") is not False:
                    filtered.append(w)
        wps = filtered

    scope = (agent.get("workplace_scope") or "single").strip().lower()
    who = aid or "system"
    header = (
        f"Workplaces for `{who}` "
        f"(scope={scope}{', coordinator' if agent.get('is_super') else ''}): "
        f"{len(wps)}"
    )
    if not wps:
        return (
            header
            + "\n(none — check Agents → workplace scope, or pair a connector)\n"
            "Do not use bash find/ls to invent workplaces; they live in Tomo's registry."
        )
    lines = [header, ""]
    for w in wps:
        lines.append("- " + _line(w))
    lines.append("")
    lines.append(
        "Use bash/read_file/… with workplace=<id|name|hostname> to target a host. "
        "Do not discover workplaces via filesystem search."
    )
    return "\n".join(lines)


__all__ = ["run"]
