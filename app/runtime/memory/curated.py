"""Curated file-backed memory — ``MEMORY.md`` + ``USER.md``.

Persistent notes the agent maintains across sessions. Entries are ``§``-delimited
Markdown fragments on disk under ``$TOMO_HOME``.

* ``USER.md`` — per-login user profile (preferences, style, habits)
* ``agents/<id>/users/<user_id>/MEMORY.md`` — per-login agent notes

A **frozen snapshot** is captured at the first turn of a session and injected
into the system prompt for the rest of that session (prefix-cache friendly).
Mid-session ``memory`` tool writes update the files immediately but do not
rewrite the prompt until the next session.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from app.core import home

_logger = logging.getLogger(__name__)

ENTRY_DELIMITER = "\n§\n"
MEMORY_CHAR_LIMIT = 4000
USER_CHAR_LIMIT = 2000

# Injected into the system prompt so the model saves without being asked.
MEMORY_GUIDANCE = (
    "You have persistent curated memory across sessions via the `memory` tool "
    "(per-user USER.md / MEMORY.md). Save durable facts **proactively** — do not "
    "wait for the user to say \"remember\" or \"save this\". "
    "Priority: preferences & corrections > environment facts > procedures. "
    "Write compact declarative facts (\"User prefers short answers\"), not "
    "self-instructions. Skip task progress, TODOs, and one-off chatter. "
    "If a file is near its char limit, replace/remove stale entries or use "
    "`remember` for longer docs — do not invent skills just to store facts. "
    "Reusable multi-step how-tos belong in `manage_skill`; long searchable "
    "facts in `remember`."
)

_HEADERS = {
    "memory": "MEMORY (your personal notes)",
    "user": "USER PROFILE (who the user is)",
}

_lock = threading.Lock()
# session_id|agent_id → rendered prompt block (frozen for the session)
_frozen: dict[str, str] = {}


def reset_freeze(*, session_id: str | None = None) -> None:
    """Drop frozen snapshots (tests or session clear)."""
    with _lock:
        if not session_id:
            _frozen.clear()
            return
        sid = session_id.strip()
        for key in list(_frozen):
            if key.startswith(f"{sid}|"):
                del _frozen[key]


def _freeze_key(session_id: str | None, agent_id: str | None) -> str:
    return f"{(session_id or '').strip()}|{(agent_id or '').strip()}"


def memories_dir(*, home_root: Path | None = None) -> Path:
    return home.memories_dir(home_root)


def user_path(
    *, user_id: str | None = None, home_root: Path | None = None
) -> Path:
    """Per-login USER.md. Defaults to the turn-bound account when unbound."""
    uid = user_id
    if uid is None:
        try:
            from app.runtime.tools.user_ctx import current_user_id

            uid = current_user_id()
        except Exception:
            uid = "web"
    return home.user_memory_path(uid, home_root)


def legacy_user_path(*, home_root: Path | None = None) -> Path:
    return home.legacy_user_memory_path(home_root)


def _resolve_uid(user_id: str | None = None) -> str:
    if user_id is not None:
        return (user_id or "").strip() or "web"
    try:
        from app.runtime.tools.user_ctx import current_user_id

        return current_user_id()
    except Exception:
        return "web"


def memory_path(
    agent_id: str | None,
    *,
    user_id: str | None = None,
    home_root: Path | None = None,
) -> Path:
    """Per-login agent MEMORY.md (turn-bound user when unbound)."""
    return home.agent_memory_path(
        agent_id, home_root, user_id=_resolve_uid(user_id)
    )


def legacy_memory_path(
    agent_id: str | None, *, home_root: Path | None = None
) -> Path:
    return home.legacy_agent_memory_path(agent_id, home_root)


def read_agent_entries(
    agent_id: str | None,
    *,
    user_id: str | None = None,
    home_root: Path | None = None,
) -> list[str]:
    """Read per-user agent MEMORY.md; ``web`` falls back to legacy shared file."""
    uid = _resolve_uid(user_id)
    path = memory_path(agent_id, user_id=uid, home_root=home_root)
    entries = read_entries(path, home_root=home_root)
    if entries:
        return entries
    if uid == "web":
        return read_entries(
            legacy_memory_path(agent_id, home_root=home_root), home_root=home_root
        )
    return []


def parse_entries(raw: str) -> list[str]:
    text = (raw or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in text.split(ENTRY_DELIMITER)]
    return [p for p in parts if p]


def _normalize_memory_text(text: str) -> str:
    return " ".join((text or "").casefold().split())


def near_duplicate(entries: list[str], content: str) -> str | None:
    """Return an existing entry that is effectively the same fact, or None.

    Match when normalized forms are equal, or one contains the other and the
    shorter side is at least 24 chars (avoids tiny substring false positives).
    """
    needle = _normalize_memory_text(content)
    if not needle:
        return None
    for entry in entries:
        hay = _normalize_memory_text(entry)
        if not hay:
            continue
        if hay == needle:
            return entry
        shorter, longer = (hay, needle) if len(hay) <= len(needle) else (needle, hay)
        if len(shorter) >= 24 and shorter in longer:
            return entry
    return None


def serialize_entries(entries: list[str]) -> str:
    cleaned = [e.strip() for e in entries if (e or "").strip()]
    if not cleaned:
        return ""
    return ENTRY_DELIMITER.join(cleaned) + "\n"


def read_entries(path: Path, *, home_root: Path | None = None) -> list[str]:
    from app.core.paths import try_under

    root = home._root(home_root)
    target = try_under(root, path)
    if target is None or not target.is_file():
        return []
    try:
        return parse_entries(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        _logger.warning("failed to read memory file %s: %s", target, exc)
        return []


def write_entries(path: Path, entries: list[str], *, home_root: Path | None = None) -> None:
    from app.core.paths import ensure_under

    root = home._root(home_root)
    target = ensure_under(root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(serialize_entries(entries), encoding="utf-8")


def _char_count(entries: list[str]) -> int:
    if not entries:
        return 0
    return len(ENTRY_DELIMITER.join(entries))


def _char_limit(target: str) -> int:
    return USER_CHAR_LIMIT if target == "user" else MEMORY_CHAR_LIMIT


def _path_for(
    target: str,
    agent_id: str | None,
    *,
    user_id: str | None = None,
    home_root: Path | None = None,
) -> Path:
    if target == "user":
        return user_path(user_id=user_id, home_root=home_root)
    return memory_path(agent_id, user_id=user_id, home_root=home_root)


def read_user_entries(
    *, user_id: str | None = None, home_root: Path | None = None
) -> list[str]:
    """Read per-user USER.md; ``web`` falls back to legacy shared file."""
    path = user_path(user_id=user_id, home_root=home_root)
    entries = read_entries(path, home_root=home_root)
    if entries:
        return entries
    uid = _resolve_uid(user_id)
    if uid == "web":
        return read_entries(legacy_user_path(home_root=home_root), home_root=home_root)
    return []


def render_block(target: str, entries: list[str]) -> str:
    header = _HEADERS.get(target, target.upper())
    if not entries:
        return ""
    body = ENTRY_DELIMITER.join(entries)
    return f"## {header}\n{body}"


def render_from_disk(
    agent_id: str | None,
    *,
    user_id: str | None = None,
    home_root: Path | None = None,
) -> str:
    """Build the live curated-memory prompt section from disk."""
    parts: list[str] = []
    user_entries = read_user_entries(user_id=user_id, home_root=home_root)
    if user_entries:
        parts.append(render_block("user", user_entries))
    if agent_id:
        mem_entries = read_agent_entries(
            agent_id, user_id=user_id, home_root=home_root
        )
        if mem_entries:
            parts.append(render_block("memory", mem_entries))
    return "\n\n".join(parts)


def prompt_block(
    agent_id: str | None,
    *,
    session_id: str | None = None,
    home_root: Path | None = None,
) -> str:
    """Return curated memory for the system prompt.

    With a ``session_id``, the first call freezes the snapshot for that
    session+agent; later turns reuse it even if files changed on disk.
    Without a session id, always reads live (tests / one-shots).
    """
    sid = (session_id or "").strip()
    if sid:
        key = _freeze_key(sid, agent_id)
        with _lock:
            cached = _frozen.get(key)
            if cached is not None:
                return cached
        block = render_from_disk(agent_id, home_root=home_root)
        with _lock:
            _frozen[key] = block
        return block
    return render_from_disk(agent_id, home_root=home_root)


def _load_target_entries(
    target: str,
    agent_id: str | None,
    *,
    home_root: Path | None = None,
) -> tuple[Path, list[str]]:
    """Resolve path + entries (USER.md and MEMORY.md are per-login)."""
    path = _path_for(target, agent_id, home_root=home_root)
    if target == "user":
        return path, read_user_entries(home_root=home_root)
    return path, read_agent_entries(agent_id, home_root=home_root)


def add_entry(
    target: str,
    content: str,
    *,
    agent_id: str | None,
    home_root: Path | None = None,
) -> dict[str, Any]:
    target = (target or "memory").strip().lower()
    if target not in ("memory", "user"):
        return {"ok": False, "error": "target must be memory or user"}
    if target == "memory" and not (agent_id or "").strip():
        return {"ok": False, "error": "agent_id required for memory target"}
    text = (content or "").strip()
    if not text:
        return {"ok": False, "error": "content is empty"}
    path, entries = _load_target_entries(target, agent_id, home_root=home_root)
    if text in entries:
        return {"ok": True, "message": "already present", "count": len(entries)}
    dup = near_duplicate(entries, text)
    if dup is not None:
        return {
            "ok": True,
            "message": "near-duplicate already present",
            "count": len(entries),
            "existing": dup[:120],
        }
    limit = _char_limit(target)
    trial = entries + [text]
    used = _char_count(entries)
    if _char_count(trial) > limit:
        return {
            "ok": False,
            "error": (
                f"would exceed {target} char limit ({used}/{limit} used). "
                "Do not create a skill as overflow. Instead: list entries, "
                "replace or remove a stale one then re-add; or use `remember` "
                "for a searchable KB fact; or `agent_state` for a short key/value."
            ),
            "count": len(entries),
            "chars": used,
            "limit": limit,
        }
    write_entries(path, trial, home_root=home_root)
    return {
        "ok": True,
        "message": f"added to {path.name}",
        "count": len(trial),
        "chars": _char_count(trial),
        "path": str(path),
    }


def replace_entry(
    target: str,
    old: str,
    new: str,
    *,
    agent_id: str | None,
    home_root: Path | None = None,
) -> dict[str, Any]:
    target = (target or "memory").strip().lower()
    if target not in ("memory", "user"):
        return {"ok": False, "error": "target must be memory or user"}
    needle = (old or "").strip()
    replacement = (new or "").strip()
    if not needle:
        return {"ok": False, "error": "old must be a non-empty substring"}
    if not replacement:
        return {"ok": False, "error": "new content is empty"}
    path, entries = _load_target_entries(target, agent_id, home_root=home_root)
    matches = [i for i, e in enumerate(entries) if needle in e]
    if not matches:
        return {"ok": False, "error": f"no entry matching {needle!r}"}
    if len(matches) > 1:
        return {
            "ok": False,
            "error": f"ambiguous match ({len(matches)} entries); use a longer unique substring",
        }
    idx = matches[0]
    updated = list(entries)
    if needle == entries[idx]:
        updated[idx] = replacement
    else:
        updated[idx] = entries[idx].replace(needle, replacement, 1)
    if _char_count(updated) > _char_limit(target):
        return {"ok": False, "error": f"would exceed {target} char limit"}
    write_entries(path, updated, home_root=home_root)
    return {
        "ok": True,
        "message": f"replaced in {path.name}",
        "count": len(updated),
        "path": str(path),
    }


def remove_entry(
    target: str,
    old: str,
    *,
    agent_id: str | None,
    home_root: Path | None = None,
) -> dict[str, Any]:
    target = (target or "memory").strip().lower()
    if target not in ("memory", "user"):
        return {"ok": False, "error": "target must be memory or user"}
    needle = (old or "").strip()
    if not needle:
        return {"ok": False, "error": "old must be a non-empty substring"}
    path, entries = _load_target_entries(target, agent_id, home_root=home_root)
    matches = [i for i, e in enumerate(entries) if needle in e]
    if not matches:
        return {"ok": False, "error": f"no entry matching {needle!r}"}
    if len(matches) > 1:
        return {
            "ok": False,
            "error": f"ambiguous match ({len(matches)} entries); use a longer unique substring",
        }
    updated = [e for i, e in enumerate(entries) if i != matches[0]]
    write_entries(path, updated, home_root=home_root)
    return {
        "ok": True,
        "message": f"removed from {path.name}",
        "count": len(updated),
        "path": str(path),
    }


def list_entries(
    target: str,
    *,
    agent_id: str | None,
    home_root: Path | None = None,
) -> dict[str, Any]:
    target = (target or "memory").strip().lower()
    if target not in ("memory", "user"):
        return {"ok": False, "error": "target must be memory or user"}
    path, entries = _load_target_entries(target, agent_id, home_root=home_root)
    return {
        "ok": True,
        "target": target,
        "path": str(path),
        "count": len(entries),
        "chars": _char_count(entries),
        "limit": _char_limit(target),
        "entries": entries,
    }


__all__ = [
    "ENTRY_DELIMITER",
    "MEMORY_CHAR_LIMIT",
    "USER_CHAR_LIMIT",
    "MEMORY_GUIDANCE",
    "reset_freeze",
    "prompt_block",
    "render_from_disk",
    "add_entry",
    "replace_entry",
    "remove_entry",
    "list_entries",
    "parse_entries",
    "near_duplicate",
    "read_entries",
    "read_user_entries",
    "read_agent_entries",
    "user_path",
    "memory_path",
    "write_entries",
]
