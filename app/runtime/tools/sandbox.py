"""Agent work-dir sandbox for file/bash tools.

Default cwd is ``$TOMO_WORK/<agent_id>`` (e.g. ``~/tomo/ops``), created on
demand. When the chat/session binds a **local** workplace (or the agent has
one and the chat did not choose “Tomo work dir”), that ``root_path`` is used
instead. **Tunnel** workplaces route tools over the connector hub.

Path arguments must stay under that root (absolute paths OK only if inside
the root). Escapes return error strings (never raise to the caller).
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
    """Clear or reset the bound agent id.

    Safe across async-generator ``aclose`` / ``GeneratorExit``: ContextVar
    tokens must be reset in the same Context that created them. When the
    consumer cancels a nested ``run_turn`` (e.g. after a delegate handoff
    yield), cleanup may run in a different Context — fall back to ``set(None)``.
    """
    if token is not None:
        try:
            _agent_id.reset(token)
        except ValueError:
            _agent_id.set(None)
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
    """Resolve a local workplace root for ``agent_id``, or ``None`` to fall back.

    Prefers turn-aware bind (session folder / mention / register_workplace).
    When the chat chose **Tomo work dir** (``force_work_dir``), ignores the
    agent's permanently assigned local workplace so UI and tools match.
    """
    try:
        from app.runtime.tools.workplace_ctx import (
            current_workplace_hint,
            current_workplace_id,
            force_work_dir,
        )

        # Explicit session/turn workplace always wins (even force_work_dir off).
        if force_work_dir() and not current_workplace_id() and not current_workplace_hint():
            return None
    except Exception:
        pass

    raw: str | None = None
    try:
        from app.runtime.tools.workplace_remote import resolve_agent_workplace

        wp = resolve_agent_workplace(agent_id)
        if wp and (wp.get("kind") or "") == "local":
            raw = (wp.get("root_path") or "").strip() or None
    except Exception:
        raw = None
    if not raw:
        # Skip agent permanent local WP when chat wants Tomo work dir.
        try:
            from app.runtime.tools.workplace_ctx import force_work_dir

            if force_work_dir():
                return None
        except Exception:
            pass
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

    Order: session/turn local workplace → else ``$TOMO_WORK/<agent>``
    (``~/tomo/<agent>`` by default).
    """
    aid = _safe_agent_id(agent_id if agent_id is not None else current_agent_id())
    wp_root = _workplace_local_root(aid)
    if wp_root is not None:
        return wp_root
    root = home.agent_work_dir(aid).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def jail_path(root: Path, relative: str) -> Path | str:
    """Resolve a path under ``root``, or return an ``Error: ...`` string.

    Relative paths join under ``root``. Absolute paths are allowed only when
    they resolve *inside* ``root`` (so a local workplace rooted at ``/`` can
    use ``/tmp/foo``; a work-dir root still rejects ``/etc/passwd``).
    ``..`` escapes outside ``root`` are rejected — unless an active
    :mod:`app.runtime.permissions.grants` outside grant covers the target.
    Never raises.
    """
    if not isinstance(relative, str):
        return "Error: path must be a string"
    text = relative.strip()
    if not text:
        return "Error: path must not be empty"
    if "\x00" in text:
        return "Error: path contains null byte"
    try:
        root_resolved = root.resolve()
        candidate = Path(text)
        if candidate.is_absolute():
            target = candidate.resolve()
        else:
            target = (root_resolved / text).resolve()
        try:
            target.relative_to(root_resolved)
        except ValueError:
            from app.runtime.permissions.grants import (
                current_outside_grant,
                path_allowed_by_grant,
            )

            if path_allowed_by_grant(target, current_outside_grant()):
                return target
            return (
                f"Error: path escapes workplace root ({root}). "
                "Use a path relative to the workplace, or an absolute path under it."
            )
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
