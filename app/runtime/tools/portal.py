"""``portal`` tool — copy files across workplaces via coordinator staging."""

from __future__ import annotations

from typing import Any

from app.runtime.portal import transfers
from app.runtime.portal.paths import list_portals
from app.runtime.tools.sandbox import current_agent_id


def run(arguments: dict[str, Any]) -> str:
    if not isinstance(arguments, dict):
        return "Error: arguments must be an object"
    action = str(arguments.get("action") or "copy").strip().lower()

    if action == "list":
        portals = list_portals()
        jobs = transfers.list_jobs(agent_id=current_agent_id())
        lines = ["Portals:"]
        if portals:
            lines.extend(f"  {p['path']}" for p in portals)
        else:
            lines.append("  (none yet — copy into /_portal/<name>/... to create)")
        lines.append("Transfers:")
        if not jobs:
            lines.append("  (none)")
        else:
            for j in jobs:
                s = j.snapshot()
                lines.append(
                    f"  {s['id']}: {s['status']} {s['percent']}% "
                    f"({s['bytes_done']}/{s['total_bytes']}) "
                    f"{s['src']} → {s['dst']}"
                )
        return "\n".join(lines)

    if action == "status":
        jid = str(arguments.get("id") or "").strip()
        if not jid:
            return "Error: id is required for status"
        job = transfers.get(jid)
        if job is None:
            return f"Error: unknown transfer {jid}"
        s = job.snapshot()
        parts = [
            f"id: {s['id']}",
            f"status: {s['status']}",
            f"progress: {s['percent']}% ({s['bytes_done']}/{s['total_bytes']} bytes)",
            f"src: {s['src']}",
            f"dst: {s['dst']}",
        ]
        if s["error"]:
            parts.append(f"error: {s['error']}")
        return "\n".join(parts)

    if action == "cancel":
        jid = str(arguments.get("id") or "").strip()
        if not jid:
            return "Error: id is required for cancel"
        job = transfers.cancel(jid)
        if job is None:
            return f"Error: unknown transfer {jid}"
        return f"Cancelled {job.id} (status={job.status})"

    if action == "copy":
        src = arguments.get("src")
        dst = arguments.get("dst")
        if not isinstance(src, str) or not src.strip():
            return "Error: src is required (/_portal/name/path or workplace_id:path)"
        if not isinstance(dst, str) or not dst.strip():
            return "Error: dst is required (/_portal/name/path or workplace_id:path)"
        force = bool(arguments.get("background") or arguments.get("async"))
        try:
            result = transfers.start_transfer(
                src.strip(),
                dst.strip(),
                agent_id=current_agent_id(),
                force_async=force,
            )
        except FileNotFoundError as exc:
            return f"Error: {exc}"
        except ValueError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            return f"Error: portal copy failed: {exc}"

        if result.get("mode") == "sync":
            return (
                f"Copied {result['bytes']} bytes: "
                f"{result['src']} → {result['dst']}"
            )
        return (
            f"Started transfer {result['id']}: {result['src']} → {result['dst']} "
            f"({result.get('total_bytes', 0)} bytes). "
            f"Poll with portal action=status id={result['id']}."
        )

    return "Error: action must be copy, status, list, or cancel"


__all__ = ["run"]
