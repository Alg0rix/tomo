"""Per-turn workplace override for tools (mention host / register_workplace / session folder)."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

_workplace_hint: ContextVar[str | None] = ContextVar(
    "tool_workplace_hint", default=None
)
_workplace_id: ContextVar[str | None] = ContextVar("tool_workplace_id", default=None)
# When True, ignore the agent's permanently assigned *local* workplace and use
# $TOMO_WORK/<agent> (chat chose "Tomo work dir"). Tunnel/SSH still work via
# explicit workplace_id / hint / workplace= on bash.
_force_work_dir: ContextVar[bool] = ContextVar("tool_force_work_dir", default=False)


def bind_workplace(
    *,
    workplace_id: str | None = None,
    hint: str | None = None,
    force_work_dir: bool = False,
) -> tuple[Token, Token, Token]:
    """Bind turn workplace context.

    * ``workplace_id`` / ``hint`` — use that workplace (session folder or host name).
    * ``force_work_dir=True`` — chat has no folder: use ``~/tomo/<agent>``, not
      the agent's default local workplace (avoids UI saying work-dir while tools
      land on /tmp because main is assigned tmp-work).
    """
    t1 = _workplace_id.set(workplace_id if workplace_id else None)
    t2 = _workplace_hint.set(hint if hint else None)
    t3 = _force_work_dir.set(bool(force_work_dir) and not workplace_id and not hint)
    return t1, t2, t3


def reset_workplace(tokens: tuple[Token, ...] | None = None) -> None:
    if tokens is None:
        _workplace_id.set(None)
        _workplace_hint.set(None)
        _force_work_dir.set(False)
        return
    vars_ = (_workplace_id, _workplace_hint, _force_work_dir)
    for i, tok in enumerate(tokens):
        if i >= len(vars_):
            break
        try:
            vars_[i].reset(tok)
        except ValueError:
            if i == 2:
                vars_[i].set(False)
            else:
                vars_[i].set(None)


def current_workplace_id() -> str | None:
    return _workplace_id.get()


def current_workplace_hint() -> str | None:
    return _workplace_hint.get()


def force_work_dir() -> bool:
    """True when this turn should use $TOMO_WORK/<agent>, not agent local WP."""
    return bool(_force_work_dir.get())


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
    "force_work_dir",
    "match_workplace",
    "strip_workplace_hint",
]
