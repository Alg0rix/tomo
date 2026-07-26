"""Active context assembly for inference.

Converts persisted session history entries (the ``ChatEntry`` replay shape
stored by the SQLite ``messages`` table) into the OpenAI-style chat message
list the LLM clients expect, and assembles the full prompt — system +
history + new user message — for a single agent turn.

History entry ``type`` values (see the foundation design spec):
``user``, ``final``, ``thinking``, ``tool_call``, ``tool_output``,
``intermediate``, ``error``, ``delegate``.

Multi-agent swarm: when ``for_agent_id`` is set, only **this** agent's tool
trails are replayed as OpenAI ``tool_calls`` / ``tool`` messages. Other
agents' finals, tools, and handoffs become attributed assistant notes
(``[From Ops]…``) so the coordinator sees specialist results without
mistaking them for its own tool runs.

``thinking`` / ``intermediate`` / ``error`` stay internal (skipped).
``delegate`` is surfaced when ``for_agent_id`` is set.

This module is pure transformation — no HTTP, no SSE, no persistence. The
``messages`` schema stores no ``tool_call_id``, so consecutive ``tool_call``
entries are grouped into one assistant message and paired with the
immediately following ``tool_output`` entries by order, using synthesised
ids (``hist_call_<n>``). Calls with no matching output get a synthetic
``role: tool`` result so the assistant ``tool_calls`` message is never left
dangling; surplus outputs beyond the number of calls are dropped.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core import config, home

_TOOL_RESULT_PREVIEW = 1200

_SYSTEM_PROMPT_PATH = config.REPO_ROOT / "defaults" / "coordinator_system.md"
_FALLBACK_PROMPT = (
    "You are Tomo, a helpful agent. Answer the user clearly and concisely, "
    "and use tools when they help."
)


def coordinator_system_prompt(path: Path | None = None) -> str:
    """Return the coordinator system prompt.

    Reads ``defaults/coordinator_system.md`` when present (falling back to a
    short constant otherwise). ``path`` is injectable so tests can exercise
    the fallback without depending on the repo file.
    """
    target = path if path is not None else _SYSTEM_PROMPT_PATH
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return _FALLBACK_PROMPT
    text = text.strip()
    return text or _FALLBACK_PROMPT


def _read_md(path: Path) -> str:
    """Read a markdown file, returning stripped text or '' when missing/blank."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""
    return text.strip()


def build_system_prompt(
    agent_id: str | None = None, *, home_root: Path | None = None
) -> str:
    """Build the system prompt for a coordinator/agent turn from ``$TOMO_HOME``.

    Resolution order (locked, Alpha spec §2.1):

    1. **Base instructions** — ``$TOMO_HOME/agents/<id>/SYSTEM.md`` when
       ``agent_id`` is given and the file is non-empty; otherwise the repo
       default via :func:`coordinator_system_prompt`.
    2. **Global persona** — ``$TOMO_HOME/SOUL.md`` is *prepended* when present.
    3. **Agent persona overlay** — ``$TOMO_HOME/agents/<id>/SOUL.md`` is
       *appended* after the base when present (only when ``agent_id`` is given).

    Sections are joined with a blank line. No secrets are read from files.
    ``home_root`` overrides the home root (tests); it defaults to
    :data:`app.core.config.TOMO_HOME`.
    """
    root = Path(home_root) if home_root is not None else config.TOMO_HOME
    parts: list[str] = []

    global_soul = _read_md(home.soul_path(root))
    if global_soul:
        parts.append(global_soul)

    base = ""
    if agent_id:
        base = _read_md(home.agent_system_path(agent_id, root))
    if not base:
        base = coordinator_system_prompt()
    parts.append(base)

    if agent_id:
        agent_soul = _read_md(home.agent_soul_path(agent_id, root))
        if agent_soul:
            parts.append(agent_soul)
        wp_block = _workplace_prompt_section(agent_id)
        if wp_block:
            parts.append(wp_block)

    return "\n\n".join(parts)


def _workplace_prompt_section(agent_id: str) -> str:
    """Describe assigned workplaces so the model can target hosts / register paths."""
    try:
        from app.services import store

        agent = store.get_agent(agent_id)
        if not agent:
            return ""
        scope = (agent.get("workplace_scope") or "single").strip().lower()
        all_wps = store.list_workplaces()
        if scope == "all":
            allowed = all_wps
            label = "all workplaces"
        elif scope == "all_tunnels":
            allowed = [w for w in all_wps if (w.get("kind") or "") == "tunnel"]
            label = "all tunnel connectors"
        else:
            ids = list(agent.get("workplace_ids") or [])
            primary = (agent.get("workplace_id") or "").strip()
            if primary and primary not in ids:
                ids = [primary] + ids
            by_id = {w["id"]: w for w in all_wps}
            allowed = [by_id[i] for i in ids if i in by_id]
            label = "assigned workplaces" if allowed else "none (local sandbox work/)"

        lines = [
            "## Workplaces",
            f"Scope: {scope} ({label}).",
            "Use register_workplace(kind=local, path=...) when the user names a local "
            "project path to debug (auto-registers and binds it for this turn).",
            "When the user names a host (e.g. aio-serv), run tools against that "
            "workplace — pass workplace= in bash if needed.",
        ]
        if allowed:
            lines.append("Available:")
            for w in allowed[:40]:
                host = (
                    w.get("host")
                    or w.get("host_detail")
                    or w.get("connector_hostname")
                    or w.get("ssh_host")
                    or w.get("root_path")
                    or ""
                )
                status = w.get("status") or ""
                bit = f"- {w.get('name')} id={w.get('id')} kind={w.get('kind')}"
                if host:
                    bit += f" host={host}"
                if status:
                    bit += f" status={status}"
                lines.append(bit)
        return "\n".join(lines)
    except Exception:
        return ""


def _history_agent_label(agent_id: str | None) -> str:
    aid = (agent_id or "").strip()
    if not aid:
        return "agent"
    try:
        from app.services import store

        agent = store.get_agent(aid)
        if agent:
            return str(agent.get("name") or aid)
    except Exception:
        pass
    return aid


def _is_self_entry(entry: dict[str, Any], for_agent_id: str | None) -> bool:
    """Whether this history row belongs to the agent currently running."""
    if not for_agent_id:
        return True
    aid = (entry.get("agent_id") or "").strip()
    if not aid:
        return True
    return aid == for_agent_id


def _preview_tool_result(text: str, limit: int = _TOOL_RESULT_PREVIEW) -> str:
    raw = text if isinstance(text, str) else str(text or "")
    raw = raw.strip()
    if len(raw) <= limit:
        return raw or "(no output)"
    return raw[:limit] + f"\n…[truncated, {len(raw)} chars]"


def _fold_foreign_tools(
    agent_id: str | None,
    call_entries: list[dict[str, Any]],
    out_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Collapse another agent's tool trail into an attributed assistant note."""
    label = _history_agent_label(agent_id)
    lines = [f"[From {label} — tool run]"]
    for idx, call in enumerate(call_entries):
        name = call.get("function") or "tool"
        args = call.get("params")
        try:
            args_s = json.dumps(args, ensure_ascii=False) if args is not None else "{}"
        except (TypeError, ValueError):
            args_s = str(args)
        if len(args_s) > 400:
            args_s = args_s[:400] + "…"
        lines.append(f"- {name}({args_s})")
        if idx < len(out_entries):
            out = out_entries[idx]
            body = _preview_tool_result(str(out.get("content") or ""))
            err = " ✗" if out.get("error") else ""
            lines.append(f"  →{err} {body}")
        else:
            lines.append("  → (missing tool result)")
    return {"role": "assistant", "content": "\n".join(lines)}


def history_to_messages(
    history: list[dict[str, Any]] | None,
    *,
    for_agent_id: str | None = None,
) -> list[dict[str, Any]]:
    """Map session history entries to OpenAI-style chat messages.

    ``user`` -> user; ``final`` -> assistant (attributed when another agent);
    self ``tool_call``/``tool_output`` -> OpenAI tool pairing; other agents'
    tools -> ``[From Name — tool run]`` notes; ``delegate`` -> swarm note when
    ``for_agent_id`` is set.
    """
    messages: list[dict[str, Any]] = []
    if not history:
        return messages

    call_counter = 0
    i = 0
    n = len(history)
    while i < n:
        entry = history[i]
        etype = entry.get("type")

        if etype == "user":
            messages.append({"role": "user", "content": entry.get("content") or ""})
            i += 1
            continue

        if etype == "final":
            content = entry.get("content") or ""
            if _is_self_entry(entry, for_agent_id):
                messages.append({"role": "assistant", "content": content})
            else:
                label = _history_agent_label(entry.get("agent_id"))
                aid = (entry.get("agent_id") or "").strip()
                header = f"[From {label}" + (f" id={aid}" if aid else "") + "]"
                body = content.strip()
                messages.append(
                    {
                        "role": "assistant",
                        "content": f"{header}\n{body}" if body else header,
                    }
                )
            i += 1
            continue

        if etype == "delegate":
            # Surface handoffs so the coordinator remembers who did what.
            if for_agent_id:
                note = (entry.get("content") or "").strip() or "Handed off to specialist"
                to_id = (entry.get("to") or entry.get("agent_id") or "").strip()
                if to_id and to_id not in note:
                    note = f"{note} → {to_id}"
                messages.append({"role": "assistant", "content": f"[Swarm] {note}"})
            i += 1
            continue

        if etype == "tool_call":
            owner = (entry.get("agent_id") or "").strip() or None
            call_entries: list[dict[str, Any]] = []
            while (
                i < n
                and history[i].get("type") == "tool_call"
                and ((history[i].get("agent_id") or "").strip() or None) == owner
            ):
                call_entries.append(history[i])
                i += 1
            out_entries: list[dict[str, Any]] = []
            while (
                i < n
                and history[i].get("type") == "tool_output"
                and ((history[i].get("agent_id") or "").strip() or None) == owner
            ):
                out_entries.append(history[i])
                i += 1
            # Also accept unattributed tool_outputs right after (legacy rows).
            while (
                i < n
                and history[i].get("type") == "tool_output"
                and not (history[i].get("agent_id") or "").strip()
                and len(out_entries) < len(call_entries)
            ):
                out_entries.append(history[i])
                i += 1

            self_tools = _is_self_entry(
                {"agent_id": owner or for_agent_id}, for_agent_id
            )
            if self_tools or not for_agent_id:
                calls: list[dict[str, Any]] = []
                for e in call_entries:
                    cid = f"hist_call_{call_counter}"
                    call_counter += 1
                    calls.append(
                        {
                            "id": cid,
                            "type": "function",
                            "function": {
                                "name": e.get("function") or "",
                                "arguments": _dumps_args(e.get("params")),
                            },
                        }
                    )
                messages.append(
                    {"role": "assistant", "content": None, "tool_calls": calls}
                )
                out_idx = 0
                for e in out_entries:
                    if out_idx < len(calls):
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": calls[out_idx]["id"],
                                "content": e.get("content") or "",
                            }
                        )
                    out_idx += 1
                while out_idx < len(calls):
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": calls[out_idx]["id"],
                            "content": "Error: missing tool result",
                        }
                    )
                    out_idx += 1
            else:
                messages.append(
                    _fold_foreign_tools(owner, call_entries, out_entries)
                )
            continue

        # thinking / intermediate / error / unknown -> skip.
        i += 1

    return messages


def build_messages(
    history: list[dict[str, Any]] | None,
    user_message: str | None = None,
    system_prompt: str | None = None,
    *,
    for_agent_id: str | None = None,
) -> list[dict[str, Any]]:
    """Assemble the full message list for one agent turn.

    Layout: ``[system] + history_to_messages(history) + [user]``. The new
    ``user_message`` is appended only when provided — callers that persist
    the user entry into history first may pass ``user_message=None``.

    Pass ``for_agent_id`` so multi-agent history attributes specialist work
    (required for the coordinator to see Ops results correctly).
    """
    prompt = system_prompt if system_prompt is not None else coordinator_system_prompt()
    messages: list[dict[str, Any]] = [{"role": "system", "content": prompt}]
    messages.extend(
        history_to_messages(history, for_agent_id=for_agent_id)
    )
    if user_message:
        messages.append({"role": "user", "content": user_message})
    return messages


def _dumps_args(params: Any) -> str:
    """Serialise tool arguments to the OpenAI ``arguments`` JSON string."""
    if isinstance(params, dict):
        return json.dumps(params)
    if params is None:
        return "{}"
    try:
        return json.dumps(params)
    except (TypeError, ValueError):
        return "{}"


__all__ = [
    "coordinator_system_prompt",
    "build_system_prompt",
    "history_to_messages",
    "build_messages",
]
