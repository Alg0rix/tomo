"""Read/write artifact bytes across local sandbox, portal, and workplaces."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core import config
from app.runtime.portal import io as portal_io
from app.runtime.portal.paths import is_portal_path
from app.runtime.tools.sandbox import current_agent_id, jail_path, resolve_work_root


def _attachments_root() -> Path:
    return (Path(config.TOMO_HOME) / "attachments").resolve()


def is_under_attachments(path: Path) -> bool:
    try:
        path.resolve().relative_to(_attachments_root())
        return True
    except (ValueError, OSError):
        return False


def _read_all_local(path: Path) -> bytes:
    return path.read_bytes()


def _read_all_location(loc: portal_io.Location) -> bytes:
    first = portal_io.read_chunk(loc, offset=0, size=portal_io.DEFAULT_CHUNK)
    total = int(first["total_size"])
    chunks = [first["data"]]
    offset = len(first["data"])
    while offset < total:
        nxt = portal_io.read_chunk(loc, offset=offset, size=portal_io.DEFAULT_CHUNK)
        data = nxt["data"]
        if not data:
            break
        chunks.append(data)
        offset += len(data)
    return b"".join(chunks)


def _write_all_location(loc: portal_io.Location, data: bytes) -> None:
    chunk = portal_io.DEFAULT_CHUNK
    if not data:
        portal_io.write_chunk(loc, b"", offset=0, is_last=True)
        return
    offset = 0
    while offset < len(data):
        piece = data[offset : offset + chunk]
        is_last = offset + len(piece) >= len(data)
        portal_io.write_chunk(loc, piece, offset=offset, is_last=is_last)
        offset += len(piece)


def resolve_source_spec(source_path: str, agent_id: str | None = None) -> tuple[str, Any]:
    """Classify source into ``('local', Path)`` or ``('portal', Location)``."""
    text = (source_path or "").strip()
    if not text:
        raise ValueError("source_path is empty")

    if is_portal_path(text) or (
        ":" in text
        and not text.startswith("/")
        and not (len(text) > 1 and text[1] == ":")  # Windows drive
    ):
        # workplace_id:path or /_portal/...
        try:
            loc = portal_io.parse_location(text)
            return "portal", loc
        except ValueError:
            # Fall through — may be a Windows-ish or odd path under sandbox
            pass

    aid = agent_id or current_agent_id()
    root = resolve_work_root(aid)
    target = jail_path(root, text)
    if isinstance(target, str):
        raise ValueError(target.replace("Error: ", "", 1))
    return "local", target


def read_source_bytes(source_path: str, agent_id: str | None = None) -> tuple[bytes, Path | None]:
    """Return ``(bytes, local_path_or_None)``. ``local_path`` set when on host FS."""
    kind, ref = resolve_source_spec(source_path, agent_id)
    if kind == "local":
        assert isinstance(ref, Path)
        if not ref.is_file():
            raise FileNotFoundError(f"Source file not found: {source_path}")
        return _read_all_local(ref), ref

    assert isinstance(ref, portal_io.Location)
    data = _read_all_location(ref)
    local: Path | None = None
    if ref.kind == "local" and ref.workplace is not None:
        try:
            from app.runtime.portal.io import _jail_local

            local = _jail_local(ref.workplace, ref.path)
        except Exception:
            local = None
    elif ref.kind == "portal":
        from app.runtime.portal.paths import resolve_portal_fs

        try:
            local = resolve_portal_fs(ref.path)
        except Exception:
            local = None
    return data, local


def delete_source(source_path: str, agent_id: str | None = None) -> str | None:
    """Best-effort delete after move. Returns warning string or None."""
    try:
        kind, ref = resolve_source_spec(source_path, agent_id)
    except ValueError as exc:
        return str(exc)
    if kind == "local":
        assert isinstance(ref, Path)
        if is_under_attachments(ref):
            return None
        try:
            ref.unlink(missing_ok=True)
        except OSError as exc:
            return f"Source file not deleted after move: {exc}"
        return None

    assert isinstance(ref, portal_io.Location)
    if ref.kind == "local" and ref.workplace is not None:
        try:
            from app.runtime.portal.io import _jail_local

            p = _jail_local(ref.workplace, ref.path)
            if is_under_attachments(p):
                return None
            p.unlink(missing_ok=True)
        except OSError as exc:
            return f"Source file not deleted after move: {exc}"
        return None
    # Remote: try delete_file RPC if available
    try:
        from app.runtime.tools.workplace_remote import _call_ssh, _call_tunnel

        wp = ref.workplace
        if not wp:
            return "Source file not deleted (missing workplace)"
        params = {"path": ref.path}
        if ref.kind == "tunnel":
            payload = _call_tunnel(wp, "delete_file", params, timeout=60.0)
        elif ref.kind == "ssh":
            payload = _call_ssh(wp, "delete_file", params)
        else:
            return None
        if not payload.get("ok"):
            return f"Source file not deleted after move: {payload.get('error') or 'delete failed'}"
    except Exception as exc:
        return f"Source file not deleted after move: {exc}"
    return None


def write_dest_bytes(
    dest_path: str, data: bytes, agent_id: str | None = None
) -> str:
    """Write bytes into sandbox/workplace. Returns destination path label."""
    text = (dest_path or "").strip()
    if not text:
        raise ValueError("dest_path is empty")

    if is_portal_path(text) or (
        ":" in text and not text.startswith("/") and not (len(text) > 1 and text[1] == ":")
    ):
        try:
            loc = portal_io.parse_location(text)
            _write_all_location(loc, data)
            return loc.label
        except ValueError:
            pass

    aid = agent_id or current_agent_id()
    # Prefer active remote workplace for relative dests
    try:
        from app.runtime.tools.workplace_remote import resolve_agent_workplace

        wp = resolve_agent_workplace(aid)
        if wp and (wp.get("kind") or "") in ("tunnel", "ssh"):
            loc = portal_io.Location(
                kind=(wp.get("kind") or "").strip().lower(),
                path=text,
                workplace_id=wp["id"],
                workplace=wp,
            )
            _write_all_location(loc, data)
            return f"{wp['id']}:{text}"
    except Exception:
        pass

    root = resolve_work_root(aid)
    target = jail_path(root, text)
    if isinstance(target, str):
        raise ValueError(target.replace("Error: ", "", 1))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return str(target)


def agent_has_remote_workplace(agent_id: str | None) -> bool:
    if not agent_id:
        return False
    try:
        from app.runtime.tools.workplace_remote import resolve_agent_workplace

        wp = resolve_agent_workplace(agent_id)
        if not wp:
            return False
        return (wp.get("kind") or "") in ("tunnel", "ssh")
    except Exception:
        return False


__all__ = [
    "agent_has_remote_workplace",
    "delete_source",
    "is_under_attachments",
    "read_source_bytes",
    "write_dest_bytes",
]
