"""Legacy unrestricted work-dir helpers (cwd is NOT OS isolation).

Restricted actions dispatch through app.runtime.isolation before these helpers.

Agent work-dir helpers for file/bash tools.

Default cwd is ``$TOMO_WORK/<agent_id>`` (e.g. ``~/tomo/ops``), created on
demand. When the chat/session binds a **local** workplace (or the agent has
one and the chat did not choose “Tomo work dir”), that ``root_path`` is used
instead. **Tunnel** workplaces route tools over the connector hub.

The main agent in a chat without a selected project uses that root as its
starting cwd, with unrestricted local paths. Other contexts require paths
under the root or an approved outside-path grant.
"""

from __future__ import annotations

from contextlib import contextmanager
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


def dispatch_execution(tool: str, arguments: dict) -> str | None:
    """Authorize and dispatch restricted work; no missing-identity fallback."""
    from app.runtime.isolation.tool_dispatch import dispatch

    return dispatch(tool, arguments)


def require_host_execution():
    """Legacy helpers must never turn restricted contexts into host paths."""
    from app.runtime.access import AccessDenied, current_execution
    from app.services.access import access

    context = access.revalidate(current_execution())
    if context.execution_mode != "unrestricted":
        raise AccessDenied("Host paths are unavailable for restricted execution")
    return context


@contextmanager
def file_execution_guard():
    """Fence unrestricted file I/O itself, not just its preceding policy check.

    Revocation may return only after this operation releases retained access.
    Restricted local file tools run in the container and never enter this
    host guard. Restricted tools with a remote active destination yield
    without host authority: their _run routes through the verified
    destination contract first, and the host path below it stays
    unreachable (no local fallback for remote destinations).
    """
    from app.runtime.access import AccessDenied, current_execution
    from app.services.access import access

    with access.execution_guard(current_execution()) as current:
        if current.execution_mode != "unrestricted":
            active = next((r for r in current.resources
                           if r.workplace_id == current.active_workplace_id), None)
            if active is not None and active.kind in ("tunnel", "ssh"):
                yield current
                return
            raise AccessDenied("Host files require explicit unrestricted execution")
        yield current


def resolve_work_root(agent_id: str | None = None) -> Path:
    """Return the absolute sandbox root for ``agent_id`` (creates if missing).

    Order: session/turn local workplace → else ``$TOMO_WORK/<agent>``
    (``~/tomo/<agent>`` by default).
    """
    context = require_host_execution()
    if context.resources:
        active = next(r for r in context.resources if r.workplace_id == context.active_workplace_id)
        if active.kind != "local":
            from app.runtime.access import AccessDenied

            raise AccessDenied("Selected remote destination cannot use host paths")
        return Path(active.root_path).resolve()

    from app.core.paths import ensure_under

    aid = _safe_agent_id(agent_id if agent_id is not None else current_agent_id())
    wp_root = _workplace_local_root(aid)
    if wp_root is not None:
        return wp_root
    base = home.work_root().resolve()
    root = ensure_under(base, aid)
    root.mkdir(parents=True, exist_ok=True)
    return root


def unrestricted_local_paths(agent_id: str | None = None) -> bool:
    """Unrestricted mode deliberately follows the host OS account, not grants."""
    require_host_execution()
    return True


def jail_path(root: Path, relative: str) -> Path | str:
    """Resolve an explicitly unrestricted host path, or return a safe error.

    Restricted file tools never call this host helper. Their I/O is performed
    inside the container; cwd/path approval is not a confinement boundary.
    """
    try:
        require_host_execution()
    except PermissionError as exc:
        return f"Error: {exc}"
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
    except OSError as exc:
        return f"Error: invalid path: {exc}"
    return target


__all__ = [
    "bind_agent",
    "reset_agent",
    "current_agent_id",
    "resolve_work_root",
    "jail_path",
    "unrestricted_local_paths",
]
