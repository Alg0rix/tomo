"""Common shell/file dispatch seam; authorize before considering transport."""
from __future__ import annotations

import json
from typing import Any

from app.runtime.access import AccessDenied, current_execution
from .backend import backend


def dispatch(tool: str, arguments: dict[str, Any]) -> str | None:
    """None means validated unrestricted destination ONLY, never failure."""
    try:
        context = current_execution()
        current = backend.access.require_tool(context, tool)
        hint = arguments.get("workplace") or arguments.get("workplace_id")
        if hint:
            # Destination-specific grants don't authorize unrelated workplaces.
            # Additional resources are paths in the same container, not routing
            # overrides. Don't interpret names against a global host catalog.
            if hint != current.active_workplace_id:
                raise AccessDenied("Workplace override is outside the selected execution destination")
        if current.execution_mode == "unrestricted":
            return None
        active = next((r for r in current.resources if r.workplace_id == current.active_workplace_id), None)
        if active is not None and active.kind in ("tunnel", "ssh"):
            # Restricted remote execution is not container work: yield so the
            # tool routes through the verified destination contract
            # (owner/resource/generation envelope + sandbox attestation).
            # Capability and generation checks run there; returning None here
            # authorizes nothing by itself.
            return None
        if tool == "bash":
            if arguments.get("background"):
                from app.services.background_jobs import manager

                job = manager.start(arguments["command"], workplace_hint=hint)
                return (f"Background job {job['id']}\nstatus: {job['status']}\n"
                        f"backend: {job['backend']}\nworkplace: {job['workplace_id']}")
            result = backend.execute(current, ["bash", "-lc", arguments["command"]],
                                     timeout=float(arguments.get("timeout") or 30))
        elif tool == "runpy":
            result = backend.execute(current, ["python", "-"], stdin=arguments["code"],
                                     timeout=float(arguments.get("timeout") or 30))
        else:
            result = backend.execute(current, ["python", "-m", "app.runtime.isolation.worker", tool],
                                     stdin=json.dumps(arguments), timeout=30)
        parts = [result.stdout.rstrip()] if result.stdout else []
        if result.stderr:
            parts.append("stderr:\n" + result.stderr.rstrip())
        if result.returncode:
            parts.append(f"exit code: {result.returncode}")
        return "\n".join(parts) or "(no output)"
    except AccessDenied as exc:
        return f"Error: {exc}"
    except (ValueError, TypeError, KeyError):
        return "Error: invalid restricted tool arguments"
    except Exception:
        return "Error: selected destination's restricted container backend failed; no host fallback"
