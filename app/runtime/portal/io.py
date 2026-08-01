"""Unified chunked binary I/O for portal + workplaces (local / tunnel / SSH)."""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.runtime.portal.paths import is_portal_path, resolve_portal_fs

_logger = logging.getLogger(__name__)

DEFAULT_CHUNK = 256 * 1024  # 256 KiB


@dataclass(frozen=True)
class Location:
    """A file endpoint: portal staging or a workplace path."""

    kind: str  # portal | local | tunnel | ssh
    path: str  # portal virtual path or workplace-relative/abs path
    workplace_id: str = ""
    workplace: dict[str, Any] | None = None

    @property
    def label(self) -> str:
        if self.kind == "portal":
            return self.path
        wid = self.workplace_id or (self.workplace or {}).get("id") or "?"
        return f"{wid}:{self.path}"


def parse_location(spec: str) -> Location:
    """Parse ``/_portal/...`` or ``workplace_id:path`` (or ``wp:id:path``)."""
    text = (spec or "").strip()
    if not text:
        raise ValueError("location is empty")
    if is_portal_path(text):
        return Location(kind="portal", path=text)

    # wp:<id>:<path> or <id>:<path>
    raw = text
    if raw.lower().startswith("wp:"):
        raw = raw[3:]
    if ":" not in raw:
        raise ValueError(
            "location must be /_portal/<name>/... or <workplace_id>:<path>"
        )
    wid, _, path = raw.partition(":")
    wid = wid.strip()
    path = path.strip() or "."
    if not wid:
        raise ValueError("missing workplace id")
    if ".." in Path(path).parts:
        raise ValueError("path traversal is not allowed")

    from app.services import store

    wp = store.get_workplace(wid)
    if not wp:
        # try name match
        for w in store.list_workplaces():
            if (w.get("name") or "").strip().lower() == wid.lower():
                wp = w
                wid = w["id"]
                break
    if not wp:
        raise ValueError(f"unknown workplace: {wid}")
    kind = (wp.get("kind") or "local").strip().lower()
    if kind not in ("local", "tunnel", "ssh"):
        raise ValueError(f"unsupported workplace kind: {kind}")
    return Location(kind=kind, path=path, workplace_id=wid, workplace=wp)


def _local_root(wp: dict[str, Any]) -> Path:
    root = (wp.get("root_path") or wp.get("path") or "").strip()
    if not root:
        raise ValueError(f"local workplace {wp.get('id')} has no root_path")
    return Path(root).expanduser().resolve()


def _jail_local(wp: dict[str, Any], path: str) -> Path:
    root = _local_root(wp)
    rel = (path or ".").lstrip("/")
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("path escapes workplace root") from exc
    return target


def stat_size(loc: Location) -> int:
    """Return file size in bytes (0 if missing for write targets — raises if read)."""
    if loc.kind == "portal":
        p = resolve_portal_fs(loc.path)
        if not p.is_file():
            raise FileNotFoundError(f"portal file not found: {loc.path}")
        return p.stat().st_size

    if loc.kind == "local":
        assert loc.workplace is not None
        p = _jail_local(loc.workplace, loc.path)
        if not p.is_file():
            raise FileNotFoundError(f"file not found: {loc.label}")
        return p.stat().st_size

    # remote: read first chunk to learn total_size
    chunk = read_chunk(loc, offset=0, size=1)
    return int(chunk.get("total_size") or chunk.get("bytes_read") or 0)


def read_chunk(loc: Location, *, offset: int = 0, size: int = DEFAULT_CHUNK) -> dict[str, Any]:
    """Read a binary chunk. Returns ``{data: bytes, bytes_read, total_size}``."""
    if loc.kind == "portal":
        p = resolve_portal_fs(loc.path)
        if not p.is_file():
            raise FileNotFoundError(f"portal file not found: {loc.path}")
        total = p.stat().st_size
        with p.open("rb") as f:
            if offset:
                f.seek(offset)
            data = f.read(size if size > 0 else None)
        return {"data": data, "bytes_read": len(data), "total_size": total}

    if loc.kind == "local":
        assert loc.workplace is not None
        p = _jail_local(loc.workplace, loc.path)
        if not p.is_file():
            raise FileNotFoundError(f"file not found: {loc.label}")
        total = p.stat().st_size
        with p.open("rb") as f:
            if offset:
                f.seek(offset)
            data = f.read(size if size > 0 else None)
        return {"data": data, "bytes_read": len(data), "total_size": total}

    # tunnel / ssh via b64 RPC
    params = {"path": loc.path, "offset": offset, "size": size}
    result = _remote_b64(loc, "read_file_b64", params)
    raw_b64 = result.get("data") or ""
    data = base64.b64decode(raw_b64) if raw_b64 else b""
    return {
        "data": data,
        "bytes_read": int(result.get("bytes_read") or len(data)),
        "total_size": int(result.get("total_size") or len(data)),
    }


def write_chunk(
    loc: Location,
    data: bytes,
    *,
    offset: int = 0,
    is_last: bool = True,
) -> None:
    """Write a binary chunk (portal/local use .part staging like the connector)."""
    if loc.kind == "portal":
        p = resolve_portal_fs(loc.path, create=True)
        part = Path(str(p) + ".part")
        if offset == 0:
            part.parent.mkdir(parents=True, exist_ok=True)
            part.write_bytes(data)
        else:
            with part.open("ab") as f:
                f.write(data)
        if is_last:
            part.replace(p)
        return

    if loc.kind == "local":
        assert loc.workplace is not None
        p = _jail_local(loc.workplace, loc.path)
        part = Path(str(p) + ".part")
        if offset == 0:
            p.parent.mkdir(parents=True, exist_ok=True)
            part.write_bytes(data)
        else:
            with part.open("ab") as f:
                f.write(data)
        if is_last:
            part.replace(p)
        return

    params = {
        "path": loc.path,
        "data": base64.b64encode(data).decode("ascii"),
        "offset": offset,
        "is_last": is_last,
    }
    _remote_b64(loc, "write_file_b64", params)


def _remote_b64(loc: Location, method: str, params: dict[str, Any]) -> dict[str, Any]:
    from app.runtime.tools.workplace_remote import _call_ssh, _call_tunnel

    wp = loc.workplace
    if not wp:
        raise ValueError("missing workplace")
    kind = loc.kind
    if kind == "tunnel":
        payload = _call_tunnel(wp, method, params, timeout=120.0)
    elif kind == "ssh":
        payload = _call_ssh(wp, method, params)
    else:
        raise ValueError(f"not a remote location: {kind}")
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error") or f"{method} failed")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"{method} returned unexpected payload")
    return result


def copy_sync(
    src: Location,
    dst: Location,
    *,
    chunk_size: int = DEFAULT_CHUNK,
    on_progress: Any | None = None,
) -> int:
    """Copy all bytes from src to dst. Returns total bytes written."""
    first = read_chunk(src, offset=0, size=chunk_size)
    total = int(first["total_size"])
    offset = 0
    data = first["data"]
    while True:
        is_last = offset + len(data) >= total
        write_chunk(dst, data, offset=offset, is_last=is_last)
        offset += len(data)
        if on_progress:
            on_progress(offset, total)
        if is_last or not data:
            break
        nxt = read_chunk(src, offset=offset, size=chunk_size)
        data = nxt["data"]
        if not data:
            # ensure final rename if empty last chunk
            if offset < total:
                write_chunk(dst, b"", offset=offset, is_last=True)
            break
    return offset


__all__ = [
    "DEFAULT_CHUNK",
    "Location",
    "parse_location",
    "stat_size",
    "read_chunk",
    "write_chunk",
    "copy_sync",
]
