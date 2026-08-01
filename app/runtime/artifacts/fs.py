"""Filesystem helpers for per-session artifact directories (Kimi-style sessionDir).

Layout::

    $TOMO_HOME/sessions/<session_id>/artifacts/<filename>

Artifacts belong to a chat session so each conversation stays quiet — no
cross-session noise on the agent.
"""

from __future__ import annotations

import os
import re
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any

from app.core import home

# Tools gated by ``agents.artifacts_enabled``.
ARTIFACT_TOOLS = frozenset({"save_artifact", "list_artifacts", "fetch_artifact"})

_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
_session_id: ContextVar[str | None] = ContextVar("artifact_session_id", default=None)

TEXT_DOCUMENT_EXTENSIONS = frozenset(
    {
        ".md",
        ".pdf",
        ".txt",
        ".csv",
        ".json",
        ".yaml",
        ".yml",
        ".xml",
        ".log",
        ".py",
        ".c",
        ".rs",
        ".js",
        ".ts",
        ".jsx",
        ".tsx",
        ".cpp",
        ".cc",
        ".cxx",
        ".h",
        ".hpp",
        ".java",
        ".go",
        ".rb",
        ".php",
        ".cs",
        ".swift",
        ".kt",
        ".scala",
        ".r",
        ".m",
        ".sh",
        ".bash",
        ".zsh",
        ".ps1",
        ".sql",
        ".html",
        ".css",
        ".scss",
        ".less",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".env",
        ".lock",
        ".diff",
        ".patch",
        ".vue",
        ".svelte",
        ".lua",
        ".pl",
        ".pm",
        ".gradle",
        ".groovy",
    }
)


def bind_session(session_id: str | None) -> Token:
    """Bind the active chat session for artifact tools (mirrors todo.bind_session)."""
    sid = (session_id or "").strip() or None
    return _session_id.set(sid)


def reset_session(token: Token | None = None) -> None:
    if token is not None:
        try:
            _session_id.reset(token)
        except ValueError:
            _session_id.set(None)
    else:
        _session_id.set(None)


def current_session_id() -> str | None:
    return _session_id.get()


def safe_session_id(session_id: str | None) -> str | None:
    sid = (session_id or "").strip()
    if not sid or not _SESSION_ID_RE.match(sid):
        return None
    if ".." in sid or "/" in sid or "\\" in sid:
        return None
    return sid


def sessions_root(*, home_root: Path | None = None) -> Path:
    return home.sessions_dir(home_root)


def session_dir(session_id: str, *, home_root: Path | None = None) -> Path:
    sid = safe_session_id(session_id)
    if not sid:
        raise ValueError("invalid session_id")
    return home.session_dir(sid, home_root)


def artifacts_dir(session_id: str, *, home_root: Path | None = None) -> Path:
    """``$TOMO_HOME/sessions/<session_id>/artifacts``."""
    return session_dir(session_id, home_root=home_root) / "artifacts"


def ensure_artifacts_dir(session_id: str, *, home_root: Path | None = None) -> Path:
    d = artifacts_dir(session_id, home_root=home_root)
    d.mkdir(parents=True, exist_ok=True)
    return d


def validate_filename(filename: str) -> str | None:
    """Return an error string if ``filename`` is unsafe, else ``None``."""
    name = (filename or "").strip()
    if not name:
        return "filename is required"
    if "/" in name or "\\" in name or ".." in name:
        return 'Invalid filename: must not contain "/", "\\", or ".."'
    if name in {".", ".."}:
        return "Invalid filename"
    return None


def category_for(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in (".html", ".htm"):
        return "html"
    if ext in (".md", ".markdown"):
        return "markdown"
    if ext == ".pdf":
        return "pdf"
    if ext in (".csv", ".tsv"):
        return "csv"
    if ext == ".json":
        return "json"
    if ext in (
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".css",
        ".scss",
        ".less",
        ".java",
        ".go",
        ".rb",
        ".php",
        ".rs",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cs",
        ".swift",
        ".kt",
        ".scala",
        ".sh",
        ".bash",
        ".zsh",
        ".ps1",
        ".sql",
        ".r",
        ".lua",
        ".pl",
        ".vue",
        ".svelte",
        ".xml",
        ".toml",
        ".ini",
        ".yaml",
        ".yml",
        ".diff",
        ".patch",
    ):
        return "code"
    if ext in TEXT_DOCUMENT_EXTENSIONS:
        return "text"
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico"):
        return "image"
    if ext in (".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a", ".wma"):
        return "sound"
    if ext in (".mp4", ".webm", ".mov", ".avi", ".mkv", ".m4v"):
        return "video"
    return "data"


def is_text_or_document(filename: str) -> bool:
    return category_for(filename) in (
        "text",
        "document",
        "html",
        "markdown",
        "csv",
        "json",
        "code",
    )


def artifact_public_url(session_id: str, filename: str) -> str:
    return f"/api/sessions/{session_id}/artifacts/{filename}"


def _safe_join(session_id: str, filename: str, *, home_root: Path | None = None) -> Path:
    err = validate_filename(filename)
    if err:
        raise ValueError(err)
    base = ensure_artifacts_dir(session_id, home_root=home_root).resolve()
    target = (base / filename).resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise ValueError("path escapes artifacts directory") from exc
    return target


def write_artifact_bytes(
    session_id: str, filename: str, data: bytes, *, home_root: Path | None = None
) -> dict[str, Any]:
    path = _safe_join(session_id, filename, home_root=home_root)
    path.write_bytes(data)
    return {
        "filename": filename,
        "filepath": str(path),
        "size": path.stat().st_size,
        "category": category_for(filename),
        "session_id": session_id,
        "url": artifact_public_url(session_id, filename),
    }


def write_artifact_text(
    session_id: str, filename: str, content: str, *, home_root: Path | None = None
) -> dict[str, Any]:
    return write_artifact_bytes(
        session_id, filename, content.encode("utf-8"), home_root=home_root
    )


def read_artifact_bytes(
    session_id: str, filename: str, *, home_root: Path | None = None
) -> bytes:
    path = _safe_join(session_id, filename, home_root=home_root)
    if not path.is_file():
        raise FileNotFoundError(f"Artifact not found: {filename}")
    return path.read_bytes()


def delete_artifact_file(
    session_id: str, filename: str, *, home_root: Path | None = None
) -> bool:
    path = _safe_join(session_id, filename, home_root=home_root)
    if not path.is_file():
        return False
    path.unlink()
    return True


def _grep_file(filepath: Path, needle: str) -> bool:
    try:
        with filepath.open("r", encoding="utf-8", errors="replace") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    return False
                if needle in chunk.lower():
                    return True
    except OSError:
        return False


def list_artifact_files(
    session_id: str,
    *,
    filter: str = "",
    grep: str = "",
    type: str = "",
    sort: str = "newest",
    limit: int = 50,
    page: int = 1,
    home_root: Path | None = None,
) -> dict[str, Any]:
    """List artifact files for one session."""
    sid = safe_session_id(session_id)
    if not sid:
        return {"files": [], "total": 0, "page": 1, "limit": limit, "pages": 0, "session_id": ""}

    d = artifacts_dir(sid, home_root=home_root)
    if not d.is_dir():
        return {
            "files": [],
            "total": 0,
            "page": 1,
            "limit": limit,
            "pages": 0,
            "session_id": sid,
        }

    filter_q = (filter or "").strip().lower()
    grep_q = (grep or "").strip().lower()
    type_f = (type or "").strip().lower()
    sort_p = (sort or "").strip().lower() or "newest"
    if sort_p not in ("newest", "updated", "alpha", "alpha_desc"):
        sort_p = "newest"
    valid_types = ("all", "document", "text", "image", "sound", "video", "data")
    if type_f and type_f not in valid_types:
        type_f = ""

    try:
        lim = int(limit)
    except (TypeError, ValueError):
        lim = 50
    lim = max(1, min(lim, 200))
    try:
        pg = max(1, int(page))
    except (TypeError, ValueError):
        pg = 1

    files: list[dict[str, Any]] = []
    try:
        names = os.listdir(d)
    except OSError:
        return {
            "files": [],
            "total": 0,
            "page": 1,
            "limit": lim,
            "pages": 0,
            "session_id": sid,
        }

    for fname in names:
        fpath = d / fname
        if not fpath.is_file():
            continue
        if filter_q and filter_q not in fname.lower():
            continue
        cat = category_for(fname)
        if type_f and type_f != "all" and cat != type_f:
            continue
        if grep_q:
            if is_text_or_document(fname):
                if not _grep_file(fpath, grep_q):
                    continue
            else:
                continue
        try:
            st = fpath.stat()
        except OSError:
            continue
        files.append(
            {
                "filename": fname,
                "size": st.st_size,
                "modified": st.st_mtime,
                "category": cat,
                "url": artifact_public_url(sid, fname),
            }
        )

    if sort_p in ("newest", "updated"):
        files.sort(key=lambda f: f["modified"], reverse=True)
    elif sort_p == "alpha":
        files.sort(key=lambda f: f["filename"].lower())
    else:
        files.sort(key=lambda f: f["filename"].lower(), reverse=True)

    total = len(files)
    pages = (total + lim - 1) // lim if total else 0
    start = (pg - 1) * lim
    return {
        "files": files[start : start + lim],
        "total": total,
        "page": pg,
        "limit": lim,
        "pages": pages,
        "session_id": sid,
    }


def stats_for_session(
    session_id: str, *, home_root: Path | None = None
) -> dict[str, Any]:
    full = list_artifact_files(session_id, limit=200, home_root=home_root)
    by_cat: dict[str, int] = {}
    total_size = 0
    for f in full["files"]:
        by_cat[f["category"]] = by_cat.get(f["category"], 0) + 1
        total_size += int(f["size"] or 0)
    return {
        "count": full["total"],
        "total_size": total_size,
        "by_category": by_cat,
        "session_id": full.get("session_id") or session_id,
    }


# Back-compat aliases used during the agent-scoped rollout.
def stats_for_agent(agent_id: str, *, home_root: Path | None = None) -> dict[str, Any]:
    """Deprecated: agent-scoped stats. Prefer :func:`stats_for_session`."""
    _ = agent_id, home_root
    return {"count": 0, "total_size": 0, "by_category": {}}
