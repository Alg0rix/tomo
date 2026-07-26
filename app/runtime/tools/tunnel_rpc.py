"""Route bash/file tools to a live Tomo Connector when the agent workplace is tunnel."""

from __future__ import annotations

from typing import Any

from app.runtime.tools.sandbox import current_agent_id
from app.workplaces.hub import hub

_DEFAULT_TIMEOUT = 60.0
_MAX_TIMEOUT = 120.0


def _timeout_seconds(raw: Any, default: float = _DEFAULT_TIMEOUT) -> float:
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value <= 0:
        return default
    return min(value, _MAX_TIMEOUT)


def agent_tunnel_workplace_id(agent_id: str | None = None) -> str | None:
    """Return tunnel workplace id for the agent if assigned, else ``None``."""
    aid = agent_id if agent_id is not None else current_agent_id()
    if not aid:
        return None
    try:
        from app.services import store

        wp = store.resolve_agent_workplace(aid)
    except Exception:
        return None
    if not wp or (wp.get("kind") or "").strip().lower() != "tunnel":
        return None
    return (wp.get("id") or "").strip() or None


def try_tunnel_rpc(
    method: str,
    params: dict[str, Any],
    *,
    timeout: float | None = None,
) -> str | None:
    """If agent is on a tunnel workplace, run RPC and return result string.

    Returns ``None`` when the agent is not on a tunnel workplace (caller should
    run local logic). Returns an ``Error: ...`` string when tunnel is offline
    or the RPC fails — never raises.
    """
    wid = agent_tunnel_workplace_id()
    if not wid:
        return None
    if not hub.is_online(wid):
        return "Error: tunnel workplace is offline (connector not connected)"
    to = _timeout_seconds(timeout if timeout is not None else params.get("timeout"))
    # Don't double-send timeout inside bash params if we already apply RPC timeout.
    rpc_params = dict(params)
    result = hub.call(wid, method, rpc_params, timeout=to)
    if not result.get("ok"):
        err = result.get("error") or "RPC failed"
        return f"Error: {err}"
    out = result.get("result")
    if out is None:
        return "(no output)"
    return str(out)


__all__ = ["agent_tunnel_workplace_id", "try_tunnel_rpc"]
