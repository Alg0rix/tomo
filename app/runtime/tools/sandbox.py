"""Agent work-dir sandbox for file/bash tools.

Default cwd is ``$TOMO_HOME/agents/<id>/work`` (created on demand). When the
agent has a **local** workplace with an existing ``root_path``, that path is
used instead. **Tunnel** workplaces route bash/file tools over the connector
hub (see :mod:`app.runtime.tools.tunnel_rpc`) — local cwd is not used.
SSH workplaces still use the local ``work/`` fallback for bash/file.

Path arguments must stay under that root — absolute paths and ``..`` escapes
are rejected as error strings (never raise to the caller).
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from pathlib import Path

from app.core import home

_agent_id: ContextVar[str | None] = ContextVar("tool_sandbox_agent_id", default=None)

_DEFAULT_AGENT = "_default"


def bind_agent(agent_id: str | None) -> Token:
    """Bind the agent whose ``work/`` dir tools should use."""
    return _agent_id.set(agent_id if isinstance(agent_id, str) and agent_id else None)


def reset_agent(token: Token | None = None) -> None:
    """Clear or reset the bound agent id."""
    if token is not None:
        _agent_id.reset(token)
    else:
        _agent_id.set(None)


def current_agent_id() -> str | None:
    return _agent_id.get()


def _safe_agent_id(agent_id: str | None) -> str:
    """Collapse unsafe / empty ids to ``_default`` (no path separators)."""
    if not isinstance(agent_id, str):
        return _DEFAULT_AGENT
    text = agent_id.strip()
    if not text or "/" in text or "\\" in text or ".." in text or text in {".", ".."}:
        return _DEFAULT_AGENT
    return text


def _workplace_local_root(agent_id: str) -> Path | None:
    """Resolve a local workplace root for ``agent_id``, or ``None`` to fall back."""
    try:
        from app.services import store

        raw = store.resolve_agent_workplace_root(agent_id)
    except Exception:
        return None
    if not raw:
        return None
    path = Path(raw).expanduser()
    try:
        if path.is_dir():
            return path.resolve()
    except OSError:
        return None
    return None


def resolve_work_root(agent_id: str | None = None) -> Path:
    """Return the absolute sandbox root for ``agent_id`` (creates if missing).

    Uses the bound ContextVar when ``agent_id`` is omitted. Prefers a local
    workplace root when assigned and the path exists; otherwise creates the
    agent ``work/`` directory so bash/file tools have a real cwd.
    """
    aid = _safe_agent_id(agent_id if agent_id is not None else current_agent_id())
    wp_root = _workplace_local_root(aid)
    if wp_root is not None:
        return wp_root
    root = home.agent_work_dir(aid).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def jail_path(root: Path, relative: str) -> Path | str:
    """Resolve ``relative`` under ``root``, or return an ``Error: ...`` string.

    Rejects absolute paths and any path that escapes ``root`` after resolve
    (including ``..`` components). Never raises.
    """
    if not isinstance(relative, str):
        return "Error: path must be a string"
    text = relative.strip()
    if not text:
        return "Error: path must not be empty"
    if "\x00" in text:
        return "Error: path contains null byte"
    candidate = Path(text)
    if candidate.is_absolute():
        return "Error: absolute paths are not allowed"
    try:
        root_resolved = root.resolve()
        target = (root_resolved / text).resolve()
        target.relative_to(root_resolved)
    except ValueError:
        return "Error: path escapes sandbox cwd"
    except OSError as exc:
        return f"Error: invalid path: {exc}"
    return target


__all__ = [
    "bind_agent",
    "reset_agent",
    "current_agent_id",
    "resolve_work_root",
    "jail_path",
]
