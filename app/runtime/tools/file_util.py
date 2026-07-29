"""Shared helpers for file tools (read formatting, fuzzy path hints)."""

from __future__ import annotations

import difflib
from pathlib import Path

_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tomo"}


def format_numbered_page(
    text: str,
    *,
    offset: int = 1,
    limit: int | None = 500,
    max_chars: int = 100_000,
) -> str:
    """Return 1-based ``N|line`` page of ``text`` with continuation hints.

    Better than Hermes/Evonic plain slices: includes total lines, range header,
    and explicit next_offset when more content remains.
    """
    if offset < 1:
        offset = 1
    lines = text.splitlines()
    total = len(lines)
    if total == 0:
        return "(empty file)"

    start = offset - 1
    if start >= total:
        return (
            f"Error: offset {offset} is past end of file "
            f"({total} line{'s' if total != 1 else ''}). "
            f"Use offset=1..{total}."
        )

    if limit is None or limit <= 0:
        limit = total
    limit = min(limit, 2000)

    end = min(start + limit, total)
    page = lines[start:end]
    body_parts: list[str] = []
    size = 0
    truncated_mid = False
    last_i = start
    for i, line in enumerate(page, start=offset):
        row = f"{i}|{line}"
        # +1 for newline when joining
        add = len(row) + (1 if body_parts else 0)
        if size + add > max_chars and body_parts:
            truncated_mid = True
            break
        body_parts.append(row)
        size += add
        last_i = i

    header = f"# {path_label(offset, last_i, total)}"
    out = header + "\n" + "\n".join(body_parts)
    if last_i < total or truncated_mid:
        nxt = last_i + 1
        out += (
            f"\n\n… more content after line {last_i} "
            f"({total - last_i} line(s) left). "
            f"Continue with offset={nxt}."
        )
    return out


def path_label(start: int, end: int, total: int) -> str:
    return f"lines {start}-{end} of {total}"


def suggest_similar_paths(root: Path, want: str, *, limit: int = 5) -> list[str]:
    """Return relative paths under ``root`` similar to ``want`` (typo help)."""
    want_name = Path(want).name.casefold()
    want_full = want.replace("\\", "/").casefold()
    if not want_name and not want_full:
        return []
    candidates: list[str] = []
    try:
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if any(part in _SKIP_DIRS for part in p.parts):
                continue
            try:
                rel = p.relative_to(root).as_posix()
            except ValueError:
                continue
            candidates.append(rel)
            if len(candidates) > 5000:
                break
    except OSError:
        return []

    scored: list[tuple[float, str]] = []
    for rel in candidates:
        name = Path(rel).name.casefold()
        ratio = difflib.SequenceMatcher(None, want_name, name).ratio()
        if want_full and want_full in rel.casefold():
            ratio = max(ratio, 0.85)
        if ratio >= 0.55:
            scored.append((ratio, rel))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [r for _, r in scored[:limit]]


def not_found_message(root: Path, path_arg: str) -> str:
    hints = suggest_similar_paths(root, path_arg)
    msg = f"Error: file not found: {path_arg}"
    if hints:
        msg += ". Did you mean: " + ", ".join(hints)
    return msg


def parse_positive_int(raw: object, default: int, *, name: str, minimum: int = 1) -> int | str:
    if raw is None:
        return default
    try:
        if isinstance(raw, bool):
            return f"Error: '{name}' must be an integer"
        val = int(raw)
    except (TypeError, ValueError):
        return f"Error: '{name}' must be an integer"
    if val < minimum:
        return f"Error: '{name}' must be >= {minimum}"
    return val


__all__ = [
    "format_numbered_page",
    "not_found_message",
    "parse_positive_int",
    "suggest_similar_paths",
]
