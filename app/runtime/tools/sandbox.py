"""Agent work-dir sandbox for file/bash tools (Alpha Slice C).

Default cwd is ``$TOMO_HOME/agents/<id>/work`` (created on demand). When no
agent is bound, tools use ``$TOMO_HOME/agents/_default/work``. Path arguments
must stay under that root — absolute paths and ``..`` escapes are rejected as
error strings (never raise to the caller).

Workplace-backed cwd (local/SSH) lands in Slice D; until then this local
``work/`` tree is the documented sandbox.
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


def resolve_work_root(agent_id: str | None = None) -> Path:
    """Return the absolute sandbox root for ``agent_id`` (creates if missing).

    Uses the bound ContextVar when ``agent_id`` is omitted. Always creates the
    directory so bash/file tools have a real cwd.
    """
    aid = _safe_agent_id(agent_id if agent_id is not None else current_agent_id())
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
