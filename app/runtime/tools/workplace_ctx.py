"""Per-turn workplace override for tools (mention host / register_workplace)."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

_workplace_hint: ContextVar[str | None] = ContextVar(
    "tool_workplace_hint", default=None
)
_workplace_id: ContextVar[str | None] = ContextVar("tool_workplace_id", default=None)


def bind_workplace(
    *, workplace_id: str | None = None, hint: str | None = None
) -> tuple[Token, Token]:
    t1 = _workplace_id.set(workplace_id if workplace_id else None)
    t2 = _workplace_hint.set(hint if hint else None)
    return t1, t2


def reset_workplace(tokens: tuple[Token, Token] | None = None) -> None:
    if tokens is None:
        _workplace_id.set(None)
        _workplace_hint.set(None)
        return
    for tok in tokens:
        try:
            # We don't know which var each token belongs to — reset both carefully.
            pass
        except Exception:
            pass
    try:
        _workplace_id.reset(tokens[0])
    except ValueError:
        _workplace_id.set(None)
    try:
        _workplace_hint.reset(tokens[1])
    except ValueError:
        _workplace_hint.set(None)


def current_workplace_id() -> str | None:
    return _workplace_id.get()


def current_workplace_hint() -> str | None:
    return _workplace_hint.get()


def match_workplace(
    workplaces: list[dict[str, Any]], query: str
) -> dict[str, Any] | None:
    """Match workplace by id, name, hostname, host, or host_detail token."""
    raw = (query or "").strip().lstrip("@")
    if not raw:
        return None
    needle = raw.casefold()
    needle_n = needle.replace(" ", "").replace("_", "").replace("-", "")

    def score(wp: dict[str, Any]) -> int:
        fields = [
            str(wp.get("id") or ""),
            str(wp.get("name") or ""),
            str(wp.get("host") or ""),
            str(wp.get("host_detail") or ""),
            str(wp.get("connector_hostname") or ""),
            str(wp.get("ssh_host") or ""),
            str(wp.get("connector_remote_ip") or ""),
        ]
        best = 0
        for f in fields:
            if not f:
                continue
            fc = f.casefold()
            fn = fc.replace(" ", "").replace("_", "").replace("-", "")
            if fc == needle or fn == needle_n:
                return 100
            if fc.startswith(needle) or needle in fc or needle_n in fn:
                best = max(best, 50)
        return best

    ranked = [(score(w), w) for w in workplaces]
    ranked = [(s, w) for s, w in ranked if s > 0]
    if not ranked:
        return None
    ranked.sort(key=lambda x: -x[0])
    if len(ranked) > 1 and ranked[0][0] == ranked[1][0] and ranked[0][0] < 100:
        return None  # ambiguous prefix
    return ranked[0][1]


def strip_workplace_hint(
    text: str, workplaces: list[dict[str, Any]]
) -> tuple[str, str | None]:
    """If last token matches a workplace, return (stripped_text, hint)."""
    if not isinstance(text, str) or not text.strip():
        return text, None
    parts = text.strip().split()
    if len(parts) < 2:
        return text, None
    # "on aio-serv" / "at host" / "via workplace"
    if parts[-2].casefold() in ("on", "at", "via", "workplace"):
        cand = parts[-1]
        if match_workplace(workplaces, cand):
            rest = " ".join(parts[:-2]).strip()
            return rest or text, cand
    # Prefer last token; also try last two tokens joined (multi-word names).
    candidates = [parts[-1]]
    if len(parts) >= 3:
        candidates.insert(0, parts[-2] + " " + parts[-1])
    for cand in candidates:
        if match_workplace(workplaces, cand):
            if " " in cand:
                rest = " ".join(parts[:-2]).strip()
            else:
                rest = " ".join(parts[:-1]).strip()
            return rest or text, cand
    return text, None


__all__ = [
    "bind_workplace",
    "reset_workplace",
    "current_workplace_id",
    "current_workplace_hint",
    "match_workplace",
    "strip_workplace_hint",
]
