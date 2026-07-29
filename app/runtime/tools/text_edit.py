"""Pure text-edit helpers for ``str_replace`` and ``patch`` tools.

No filesystem I/O — callers pass content in and write the result out.
Shared by local sandbox, tunnel RPC formatting, and SSH workplace paths.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

SEARCH_WINDOW = 80

# Smart / fancy quotes → ASCII (LLM copy from read_file can mangle these).
_QUOTE_MAP = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u2032": "'",
        "\u2033": '"',
    }
)


def normalize_quotes(s: str) -> str:
    return s.translate(_QUOTE_MAP)


def unescape_llm(s: str) -> str:
    """Undo common LLM double-escaping in tool args."""
    return s.replace('\\"', '"').replace("\\'", "'")


def normalize_for_match(s: str) -> str:
    """Normalize for fuzzy compare: unescape + decode ``\\uXXXX`` literals."""
    s = unescape_llm(s)
    return re.sub(
        r"\\u([0-9a-fA-F]{4})",
        lambda m: chr(int(m.group(1), 16)),
        s,
    )


def reencode_unicode_escapes(s: str) -> str:
    """Encode non-ASCII as ``\\uXXXX`` so matches files that store escapes."""
    out: list[str] = []
    for ch in s:
        o = ord(ch)
        if o < 128:
            out.append(ch)
        else:
            out.append(f"\\u{o:04x}")
    return "".join(out)


# ---------------------------------------------------------------------------
# str_replace
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplaceMatch:
    old: str
    new: str
    occurrences: int
    tier: str  # exact | unescape | unicode_escape | quote


def find_replace_match(content: str, old: str, new: str) -> ReplaceMatch:
    """Locate ``old`` in ``content`` with progressive fuzzy tiers.

    Returns the effective strings to use for ``str.replace`` so the file's
    encoding conventions (literal ``\\uXXXX``, smart quotes) are preserved.
    """
    n = content.count(old)
    if n > 0:
        return ReplaceMatch(old, new, n, "exact")

    unesc_old = unescape_llm(old)
    if unesc_old != old:
        n = content.count(unesc_old)
        if n > 0:
            return ReplaceMatch(unesc_old, unescape_llm(new), n, "unescape")

    reenc_old = reencode_unicode_escapes(old)
    if reenc_old != old:
        n = content.count(reenc_old)
        if n > 0:
            return ReplaceMatch(
                reenc_old, reencode_unicode_escapes(new), n, "unicode_escape"
            )

    # File has smart quotes, agent sent straight (or reverse).
    norm_old = normalize_quotes(old)
    norm_content = normalize_quotes(content)
    if norm_old in norm_content:
        idx = norm_content.index(norm_old)
        # Map length in normalized space ≈ original when only quote chars differ.
        effective_old = content[idx : idx + len(norm_old)]
        # Prefer exact slice of original content if lengths match.
        if content[idx : idx + len(old)] and normalize_quotes(
            content[idx : idx + len(old)]
        ) == norm_old:
            effective_old = content[idx : idx + len(old)]
        n = content.count(effective_old)
        if n > 0:
            return ReplaceMatch(effective_old, normalize_quotes(new), n, "quote")

    return ReplaceMatch(old, new, 0, "none")


def apply_str_replace(
    content: str,
    old: str,
    new: str,
    *,
    count: int = 1,
) -> tuple[str, int] | str:
    """Apply string replace. Returns ``(new_content, n)`` or an error string."""
    if not old:
        return "Error: 'old_string' must not be empty"
    if count < 1 and count != -1:
        return "Error: 'count' must be >= 1, or -1 to replace all"

    match = find_replace_match(content, old, new)
    if match.occurrences == 0:
        hint = _close_match_hint(content, old)
        return (
            "Error: old_string not found in file. "
            "Action: call read_file and copy the exact text to replace."
            f"{hint}"
        )

    want = match.occurrences if count == -1 else count
    if match.occurrences != want and count != -1:
        return (
            f"Error: old_string found {match.occurrences} time(s), "
            f"but count={count}. "
            "Add more surrounding context to make old_string unique, "
            f"or set count={match.occurrences} (or count=-1 for all)."
        )

    limit = match.occurrences if count == -1 else count
    updated = content.replace(match.old, match.new, limit)
    return updated, limit


def _close_match_hint(content: str, old: str) -> str:
    norm = normalize_quotes(old)
    if norm != old and norm in content:
        return " Hint: smart/straight quote mismatch — re-copy from read_file."
    unesc = unescape_llm(old)
    if unesc != old and unesc in content:
        return " Hint: extra backslash escapes in old_string — use raw text."
    # First non-empty line of old as anchor
    needle = next((ln.strip() for ln in old.splitlines() if ln.strip()), "")
    if needle and len(needle) >= 4:
        for i, line in enumerate(content.splitlines(), 1):
            if needle in line or line.strip() == needle:
                return f" Hint: similar text near line {i}."
    return ""


# ---------------------------------------------------------------------------
# unified diff patch
# ---------------------------------------------------------------------------

HunkLine = tuple[Literal[" ", "-", "+"], str, bool]  # op, text, no_newline


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[HunkLine]


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_hunks(patch_text: str) -> list[Hunk]:
    """Parse unified-diff hunks (git headers ignored)."""
    hunks: list[Hunk] = []
    current: Hunk | None = None

    for raw in patch_text.splitlines():
        line = raw.rstrip("\r\n")

        if re.match(
            r"^(diff --git|index |old mode|new mode|deleted file|new file)",
            line,
        ):
            continue
        if line.startswith("--- ") or line.startswith("+++ "):
            if current is not None:
                hunks.append(current)
                current = None
            continue

        m = _HUNK_RE.match(line)
        if m:
            if current is not None:
                hunks.append(current)
            current = Hunk(
                old_start=int(m.group(1)),
                old_count=int(m.group(2)) if m.group(2) is not None else 1,
                new_start=int(m.group(3)),
                new_count=int(m.group(4)) if m.group(4) is not None else 1,
                lines=[],
            )
            continue

        if current is None:
            continue

        if line.startswith("\\ "):
            if current.lines:
                op, txt, _ = current.lines[-1]
                current.lines[-1] = (op, txt, True)
            continue

        if line.startswith("-"):
            current.lines.append(("-", line[1:], False))
        elif line.startswith("+"):
            current.lines.append(("+", line[1:], False))
        elif line.startswith(" "):
            current.lines.append((" ", line[1:], False))
        else:
            # Bare context (some models omit the leading space)
            current.lines.append((" ", line, False))

    if current is not None:
        hunks.append(current)
    return hunks


def _find_hunk_pos(
    lines: list[str], hunk_lines: list[HunkLine], stated_pos: int
) -> tuple[int, str | None]:
    """Return ``(0-based pos, match_tier)`` or ``(-1, None)``."""
    to_match = [(op, txt) for op, txt, _ in hunk_lines if op in (" ", "-")]
    if not to_match:
        pos = max(0, min(stated_pos, len(lines)))
        return pos, None

    match_len = len(to_match)
    window = SEARCH_WINDOW

    def scan(
        transform,
    ) -> int:
        want = [transform(t) for _, t in to_match]
        got = [transform(l.rstrip("\r\n")) for l in lines]
        # Window first
        for delta in range(window + 1):
            for sign in [0] if delta == 0 else [1, -1]:
                pos = stated_pos + sign * delta
                if pos < 0 or pos + match_len > len(got):
                    continue
                if all(got[pos + i] == want[i] for i in range(match_len)):
                    return pos
        # Full file
        for pos in range(len(got) - match_len + 1):
            if all(got[pos + i] == want[i] for i in range(match_len)):
                return pos
        return -1

    # Tier 1: trailing-ws stripped
    pos = scan(lambda s: s.rstrip())
    if pos >= 0:
        return pos, None

    # Tier 2: indent-tolerant (all ws collapsed at edges)
    pos = scan(lambda s: s.strip())
    if pos >= 0:
        return pos, "indent"

    # Tier 3: LLM unescape + \uXXXX
    pos = scan(lambda s: normalize_for_match(s).rstrip())
    if pos >= 0:
        return pos, "unescape"

    # Tier 4: quote normalize
    pos = scan(lambda s: normalize_quotes(s).rstrip())
    if pos >= 0:
        return pos, "quote"

    # Tier 5: tabs ↔ 4 spaces + strip
    def tab_norm(s: str) -> str:
        return s.expandtabs(4).rstrip()

    pos = scan(tab_norm)
    if pos >= 0:
        return pos, "tabs"

    return -1, None


def _find_first_anchor(lines: list[str], hunk_lines: list[HunkLine]) -> int:
    for op, txt, _ in hunk_lines:
        if op in (" ", "-"):
            needle = txt.rstrip()
            for i, line in enumerate(lines):
                if line.rstrip("\r\n").rstrip() == needle:
                    return i
            break
    return -1


def apply_patch_to_content(raw: str, patch_text: str) -> dict[str, object]:
    """Apply unified diff to an in-memory string.

    Returns ``{"result": "success", "content": str, "hunks_applied": int}``
    or ``{"error": str}``.
    """
    hunks = parse_hunks(patch_text)
    if not hunks:
        return {
            "error": (
                "No valid hunks found in patch. Need @@ headers. "
                "For simple edits prefer str_replace."
            )
        }

    crlf = "\r\n" in raw
    content = raw.replace("\r\n", "\n")

    if content.endswith("\n"):
        lines = content[:-1].split("\n") if content else []
        trailing_newline = True
        if content == "\n":
            lines = [""]
    elif content:
        lines = content.split("\n")
        trailing_newline = False
    else:
        lines = []
        trailing_newline = False

    offset = 0

    for hunk in hunks:
        hunk_lines = hunk.lines

        if hunk.old_count == 0:
            insert_pos = hunk.new_start - 1 + offset
            insert_pos = max(0, min(insert_pos, len(lines)))
            new_lines = [txt for op, txt, _ in hunk_lines if op == "+"]
            lines = lines[:insert_pos] + new_lines + lines[insert_pos:]
            offset += len(new_lines)
            if new_lines:
                trailing_newline = True
            continue

        stated_pos = hunk.old_start - 1 + offset
        pos, match_hint = _find_hunk_pos(lines, hunk_lines, stated_pos)

        if pos == -1:
            anchor = _find_first_anchor(lines, hunk_lines)
            hint = f" (anchor near line {anchor + 1})" if anchor >= 0 else ""
            read_off = max(1, hunk.old_start - 20)
            return {
                "error": (
                    f"Context not found for hunk at line {hunk.old_start}"
                    f"{hint}. Action: read_file(offset={read_off}) and "
                    "rebuild the patch from current content."
                )
            }

        result_lines: list[str] = []
        file_idx = pos
        for op, txt, _ in hunk_lines:
            if op == " ":
                result_lines.append(lines[file_idx])
                file_idx += 1
            elif op == "-":
                file_idx += 1
            elif op == "+":
                if match_hint == "quote":
                    result_lines.append(normalize_quotes(txt))
                elif match_hint == "unescape":
                    result_lines.append(unescape_llm(txt))
                else:
                    result_lines.append(txt)

        consumed = sum(1 for op, _, _ in hunk_lines if op in (" ", "-"))
        produced = sum(1 for op, _, _ in hunk_lines if op in (" ", "+"))
        lines = lines[:pos] + result_lines + lines[pos + consumed :]
        offset += produced - consumed

    result = "\n".join(lines)
    if trailing_newline and lines:
        result += "\n"
    if crlf:
        result = result.replace("\n", "\r\n")

    return {
        "result": "success",
        "content": result,
        "hunks_applied": len(hunks),
    }


def is_create_new_file_patch(patch_text: str) -> bool:
    hunks = parse_hunks(patch_text)
    return bool(hunks) and all(h.old_start == 0 and h.old_count == 0 for h in hunks)


__all__ = [
    "ReplaceMatch",
    "Hunk",
    "apply_patch_to_content",
    "apply_str_replace",
    "find_replace_match",
    "is_create_new_file_patch",
    "normalize_for_match",
    "normalize_quotes",
    "parse_hunks",
    "reencode_unicode_escapes",
    "unescape_llm",
]
