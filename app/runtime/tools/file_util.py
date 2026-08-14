"""Shared helpers for file tools (read formatting, fuzzy path hints)."""

from __future__ import annotations

import difflib
from pathlib import Path

_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tomo"}

# Per-line clamp: a single hostile mega-line (minified bundle, base64 blob)
# must not be able to consume the whole byte budget by itself.
_LINE_CLAMP = 2000


def format_numbered_page(
    text: str,
    *,
    offset: int = 1,
    limit: int | None = 500,
    max_chars: int = 100_000,
) -> str:
    """Return 1-based ``N|line`` page of ``text`` with continuation hints.

    Includes total lines, range header,
    and explicit next_offset when more content remains.
    """
    if offset < 1:
        offset = 1
    lines = text.splitlines()
    total = len(lines)
    if total == 0:
        return "(file is empty)"

    start = offset - 1
    if start >= total:
        # Informative note, not an error: the tool worked, the offset is just
        # past the end. No "Error:" prefix — avoids red-painting a world fact.
        return (
            f"Note: offset {offset} is beyond the end of the file "
            f"({total} line{'s' if total != 1 else ''} scanned). "
            f"Retry with offset=1..{total}."
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
        if len(line) > _LINE_CLAMP:
            line = line[:_LINE_CLAMP] + f"…[line truncated, {len(line)} chars total]"
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


def levenshtein_bounded(a: str, b: str, cap: int = 2) -> int:
    """Levenshtein distance between ``a``/``b``, capped at ``cap + 1``.

    Bails out early once a row's minimum exceeds ``cap`` — callers only ever
    care whether the distance is within the bound, not its exact value.
    """
    if a == b:
        return 0
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i] + [0] * len(b)
        row_min = cur[0]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            row_min = min(row_min, cur[j])
        if row_min > cap:
            return cap + 1
        prev = cur
    return prev[-1]


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
        # Bounded Levenshtein catches near-misses ratio can underrate on
        # short names (e.g. AGENT.md -> AGENTS.md, distance 1).
        if levenshtein_bounded(want_name, name, cap=2) <= 2:
            ratio = max(ratio, 0.9)
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
    """Coerce ``raw`` to an int, or return an ``Error: ...`` string.

    Fractional values (``2.5``, ``"2.5"``) are rejected outright rather than
    floored — a silently-floored offset is a silent data-loss bug waiting to
    happen. Non-numeric strings (``"2abc"``) are rejected the same way.
    """
    if raw is None:
        return default
    if isinstance(raw, bool):
        return f"Error: '{name}' must be an integer"
    if isinstance(raw, int):
        val = raw
    elif isinstance(raw, float):
        if not raw.is_integer():
            return f"Error: '{name}' must be an integer, got fractional value {raw!r}"
        val = int(raw)
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            return f"Error: '{name}' must be an integer"
        try:
            val = int(text)
        except ValueError:
            try:
                as_float = float(text)
            except ValueError:
                return f"Error: '{name}' must be an integer"
            if not as_float.is_integer():
                return f"Error: '{name}' must be an integer, got fractional value {text!r}"
            val = int(as_float)
    else:
        return f"Error: '{name}' must be an integer"
    if val < minimum:
        return f"Error: '{name}' must be >= {minimum}"
    return val


__all__ = [
    "format_numbered_page",
    "levenshtein_bounded",
    "not_found_message",
    "parse_positive_int",
    "suggest_similar_paths",
]
