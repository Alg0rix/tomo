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
mistaking them for its own tool runs. Handoff ``[Swarm]…`` notes are
shown to non-target agents only — the specialist being handed *to* must
not see them (models echo them as a fake answer).

``thinking`` / ``intermediate`` / ``error`` stay internal (skipped).
``delegate`` is surfaced when ``for_agent_id`` is set (except for the
handoff target).

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
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from app.core import config, home

_TOOL_RESULT_PREVIEW = 1200

_SYSTEM_PROMPT_PATH = config.REPO_ROOT / "defaults" / "coordinator_system.md"
_FALLBACK_PROMPT = (
    "You are Tomo, a helpful agent. Answer the user clearly and concisely, "
    "and use tools when they help."
)
_CURRENT_TIME_HEADER = "## Current time"

# Turn-scoped freeze so build_system_prompt + build_messages see one stable stamp
# (avoids mid-turn hour-boundary flips and double-inject churn).
_frozen_time_block: ContextVar[str | None] = ContextVar(
    "tomo_sys_time_block", default=None
)


def _format_time_section(now_local: object) -> str:
    """One-shot wall clock for the system prompt (not a live ticker).

    Injected **once per turn** (see :func:`freeze_prompt_clock`). For a live
    clock the agent should run ``date`` / ``date -u`` via bash.
    """
    from datetime import datetime, timezone

    if not isinstance(now_local, datetime):
        now_local = datetime.now().astimezone()
    now_utc = now_local.astimezone(timezone.utc)
    local_s = now_local.strftime("%A, %Y-%m-%d %H:%M %Z").strip()
    if not now_local.tzname() or local_s.endswith(" "):
        local_s = now_local.strftime("%A, %Y-%m-%d %H:%M %z")
    utc_s = now_utc.strftime("%A, %Y-%m-%d %H:%M UTC")
    return (
        f"{_CURRENT_TIME_HEADER}\n"
        f"Local: {local_s}\n"
        f"UTC: {utc_s}\n"
        f"This stamp is fixed for this turn. For a live clock, run bash: `date` or `date -u`."
    )


def _current_time_section() -> str:
    frozen = _frozen_time_block.get()
    if frozen is not None:
        return frozen
    from datetime import datetime

    return _format_time_section(datetime.now().astimezone())


def freeze_prompt_clock() -> object:
    """Snapshot wall clock once for this turn; later injects reuse it.

    Returns a ContextVar token for :func:`reset_prompt_clock`.
    """
    from datetime import datetime

    # If already frozen in this context, keep the same stamp.
    existing = _frozen_time_block.get()
    if existing is not None:
        return _frozen_time_block.set(existing)
    block = _format_time_section(datetime.now().astimezone())
    return _frozen_time_block.set(block)


def reset_prompt_clock(token: object | None) -> None:
    if token is None:
        return
    try:
        _frozen_time_block.reset(token)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        _frozen_time_block.set(None)


def inject_current_time(prompt: str | None) -> str:
    """Ensure *prompt* ends with the (turn-frozen) current-time block.

    Idempotent: will not stack duplicate sections. Within a frozen turn the
    stamp never changes so system text stays stable for the whole turn.
    """
    block = _current_time_section()
    text = (prompt or "").rstrip()
    if not text:
        return block
    marker = f"\n\n{_CURRENT_TIME_HEADER}\n"
    if marker in text:
        text = text.rsplit(marker, 1)[0].rstrip()
    elif text.startswith(f"{_CURRENT_TIME_HEADER}\n"):
        return block
    return f"{text}\n\n{block}"


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


def _read_md(path: Path, *, home_root: Path | None = None) -> str:
    """Read a markdown file, returning stripped text or '' when missing/blank."""
    from app.core.paths import try_under

    root = home._root(home_root)
    target = try_under(root, path)
    if target is None:
        return ""
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""
    return text.strip()


def build_system_prompt(
    agent_id: str | None = None,
    *,
    home_root: Path | None = None,
    session_id: str | None = None,
) -> str:
    """Build the system prompt for a coordinator/agent turn from ``$TOMO_HOME``.

    Resolution order (locked, Alpha spec §2.1):

    1. **Base instructions** — ``$TOMO_HOME/agents/<id>/SYSTEM.md`` when
       ``agent_id`` is given and the file is non-empty; otherwise the repo
       default via :func:`coordinator_system_prompt`.
    2. **Global persona** — ``$TOMO_HOME/SOUL.md`` is *prepended* when present.
    3. **Agent persona overlay** — ``$TOMO_HOME/agents/<id>/SOUL.md`` is
       *appended* after the base when present (only when ``agent_id`` is given).
    4. **Swarm / workplace** — live roster and workplace bindings when relevant.
    5. **Skills awareness** — compact enabled-skill catalog when the agent has
       ``list_skills`` / ``use_skill`` / ``manage_skill`` (full bodies via
       ``use_skill``).
    6. **Curated memory** — frozen ``USER.md`` + ``MEMORY.md`` snapshot for
       this session (file-backed; refreshes next session).
    7. **Current time** — local + UTC, stamped once per turn (use bash ``date``
       for a live clock).

    Sections are joined with a blank line. No secrets are read from files.
    ``home_root`` overrides the home root (tests); it defaults to
    :data:`app.core.config.TOMO_HOME`.
    """
    root = Path(home_root) if home_root is not None else config.TOMO_HOME
    parts: list[str] = []

    global_soul = _read_md(home.soul_path(root), home_root=root)
    if global_soul:
        parts.append(global_soul)

    base = ""
    if agent_id:
        base = _read_md(home.agent_system_path(agent_id, root), home_root=root)
    if not base:
        base = coordinator_system_prompt()
    parts.append(base)

    if agent_id:
        agent_soul = _read_md(home.agent_soul_path(agent_id, root), home_root=root)
        if agent_soul:
            parts.append(agent_soul)
        # Coordinator (and any agent with delegate) needs the live swarm roster.
        swarm_block = _swarm_agents_prompt_section(agent_id)
        if swarm_block:
            parts.append(swarm_block)
        wp_block = _workplace_prompt_section(agent_id)
        if wp_block:
            parts.append(wp_block)
        skills_block = _skills_prompt_section(agent_id)
        if skills_block:
            parts.append(skills_block)
        arts_block = _artifacts_prompt_section(agent_id)
        if arts_block:
            parts.append(arts_block)
        ui_block = _ui_prompt_section(agent_id)
        if ui_block:
            parts.append(ui_block)

    try:
        from app.runtime.memory.curated import MEMORY_GUIDANCE, prompt_block

        if _agent_has_memory_tool(agent_id):
            parts.append(MEMORY_GUIDANCE)
        mem = prompt_block(agent_id, session_id=session_id, home_root=root)
        if mem:
            parts.append(mem)
    except Exception:
        pass

    browser_block = _browser_prompt_section(session_id)
    if browser_block:
        parts.append(browser_block)

    # Time last so the stable prefix stays cache-friendly when the host supports it.
    return inject_current_time("\n\n".join(parts))


def _agent_has_memory_tool(agent_id: str | None) -> bool:
    """True when curated-memory guidance should be injected for this agent."""
    if not agent_id:
        return True  # coordinator fallback path still gets guidance
    try:
        from app.services import store

        return "memory" in store.get_enabled_tool_ids(agent_id)
    except Exception:
        return False


def _ui_prompt_section(agent_id: str | None) -> str:
    """Guidance for choosing declarative UI over raw HTML in chat."""
    try:
        from app.services import store

        if agent_id and "render_ui" not in store.get_enabled_tool_ids(agent_id):
            return ""
    except Exception:
        return ""
    return (
        "## Generative UI\n\n"
        "When the user benefits from an interactive form, dashboard, table, chart, "
        "diagram, or structured result, use **render_ui**. Every node MUST include "
        "a string ``type`` (card, stack, grid, text, markdown, table, chart, "
        "mermaid, badge, divider, image, link, input, select, button). Root is "
        "usually ``{type: card|stack, children: [...]}``. Prefer declarative "
        "``chart`` / ``table`` nodes — never invent custom SVG/canvas chart HTML. "
        "Do not put HTML or JavaScript in the tree. Give each interactive node a "
        "stable id and action; UI actions return as a structured `[UI action]` "
        "message. Use mode=replace for a new/full UI and reuse the same ui_id with "
        "mode=patch for small JSON updates under /tree or /state. "
        "For a full custom HTML/JS app, use **save_artifact** instead."
    )


def _skills_prompt_section(agent_id: str) -> str:
    """Skill awareness catalog (name + short description)."""
    try:
        from app.runtime.agent.skills_prompt import build_skills_system_prompt

        return build_skills_system_prompt(agent_id)
    except Exception:
        return ""


def _artifacts_prompt_section(agent_id: str) -> str:
    """Guidance when artifacts are enabled for this agent."""
    try:
        from app.runtime.artifacts.fs import current_session_id
        from app.services import store

        agent = store.get_agent(agent_id)
        if not agent or not agent.get("artifacts_enabled", True):
            return ""
        if "save_artifact" not in store.get_enabled_tool_ids(agent_id):
            return ""
        sid = current_session_id() or "<session_id>"
        url = f"/api/sessions/{sid}/artifacts/<filename>"
        return (
            "## Artifacts (this session only)\n\n"
            "When you create a user-facing deliverable (HTML page, CSV/report, PDF, image, "
            "export, slide deck, etc.), you **must** call **save_artifact** in the **same turn** "
            "— do not wait for the user to ask. Writing a file with shell/`write` alone is not enough; "
            "the Files panel and chat preview only see session artifacts.\n\n"
            "Use **save_artifact** with `filename` + `content`, or `source_path` pointing at the "
            "file you just wrote. Files are scoped to the **current chat session** under "
            f"`$TOMO_HOME/sessions/{sid}/artifacts/` and served at `{url}`. "
            "Use **list_artifacts** / **fetch_artifact** for this session only. "
            "After saving an image, embed it with "
            f'`<img src="/api/sessions/{sid}/artifacts/NAME" alt="...">`.'
        )
    except Exception:
        return ""


def _browser_prompt_section(session_id: str | None) -> str:
    """Live browser control status for this turn (no page snapshots)."""
    try:
        from app.runtime.browser.context import current_browser_user_id
        from app.runtime.browser.gateway import get_gateway

        uid = current_browser_user_id()
        if not uid and session_id:
            from app.services import store

            sess = store.get_session(session_id)
            if sess:
                uid = str(sess.get("user_id") or "") or None
        gw = get_gateway()
        connected = bool(uid and gw.is_connected(uid))
        if not connected:
            return (
                "## Browser control\n\n"
                "Status: **not connected** for this turn — `browser_*` tools are "
                "not available.\n"
                "Do **not** claim browser control is permanently impossible. "
                "Ask the user to Connect Tomo Browser (extension + Chat → "
                "Browser Control). Do **not** recommend Playwright / "
                "`--remote-debugging-port` as the default path on Tomo."
            )
        status = gw.public_status(uid or "")
        tabs = status.get("authorized_tabs") or []
        lines = [
            "## Browser control",
            "",
            "Status: **connected** — the user's real Chrome is available via the "
            "Tomo Browser extension. You **can** interactively control authorized "
            "tabs. Prefer `browser_*` tools over `web_fetch` for logged-in / "
            "open-tab work.",
            "",
            "Loop: `browser_tabs` → `browser_snapshot` → act with **refs** "
            "(`browser_click` / `browser_type` / `browser_navigate` / …) → "
            "re-snapshot after changes. Never invent CSS selectors or CDP.",
            f"Authorized tabs: {len(tabs)}",
        ]
        for t in tabs[:8]:
            if not isinstance(t, dict):
                continue
            lines.append(
                f"- [{t.get('id')}] {t.get('title') or '(untitled)'} "
                f"({t.get('domain') or t.get('url') or ''})"
            )
        if not tabs:
            lines.append(
                "- (none listed yet — call `browser_tabs`; ensure extension "
                "“Control all tabs” or authorize tabs in the popup)"
            )
        return "\n".join(lines)
    except Exception:
        return ""


def _agent_workplace_summary(agent: dict[str, Any], workplaces_by_id: dict[str, dict]) -> str:
    """Compact workplace binding for one agent (roster line)."""
    scope = (agent.get("workplace_scope") or "single").strip().lower()
    if scope == "all":
        tunnels = [
            w
            for w in workplaces_by_id.values()
            if (w.get("kind") or "").strip().lower() == "tunnel"
        ]
        online = [w for w in tunnels if w.get("online") is True]
        if online:
            names = ",".join((w.get("name") or w.get("id") or "?") for w in online[:4])
            more = f"+{len(online) - 4}" if len(online) > 4 else ""
            return f"workplaces=all(tunnels_online={names}{more})"
        return "workplaces=all(local+tunnel+ssh)"
    if scope == "all_tunnels":
        tunnels = [
            w
            for w in workplaces_by_id.values()
            if (w.get("kind") or "").strip().lower() == "tunnel"
        ]
        if not tunnels:
            return "workplaces=all_tunnels(none)"
        bits: list[str] = []
        for w in tunnels[:5]:
            name = w.get("name") or w.get("id") or "?"
            state = "online" if w.get("online") else "offline"
            bits.append(f"{name}/{state}")
        more = f"+{len(tunnels) - 5}" if len(tunnels) > 5 else ""
        return "workplaces=all_tunnels:" + ",".join(bits) + more
    ids: list[str] = list(agent.get("workplace_ids") or [])
    primary = (agent.get("workplace_id") or "").strip()
    if primary and primary not in ids:
        ids = [primary] + ids
    if not ids:
        return "workplace=none"
    labels: list[str] = []
    for wid in ids[:6]:
        w = workplaces_by_id.get(wid)
        if not w:
            labels.append(wid)
            continue
        name = w.get("name") or wid
        kind = (w.get("kind") or "?").strip().lower()
        if kind == "tunnel":
            state = "online" if w.get("online") else "offline"
            labels.append(f"{name}/{kind}/{state}")
        else:
            labels.append(f"{name}/{kind}")
    more = f"+{len(ids) - 6}" if len(ids) > 6 else ""
    return "workplace=" + ",".join(labels) + more


def _agent_local_workplaces(
    agent: dict[str, Any], workplaces_by_id: dict[str, dict]
) -> list[dict[str, Any]]:
    """Local workplaces bound to this agent (not tunnel/ssh)."""
    scope = (agent.get("workplace_scope") or "single").strip().lower()
    if scope == "all":
        return [
            w
            for w in workplaces_by_id.values()
            if (w.get("kind") or "").strip().lower() == "local"
        ]
    if scope == "all_tunnels":
        return []
    ids: list[str] = list(agent.get("workplace_ids") or [])
    primary = (agent.get("workplace_id") or "").strip()
    if primary and primary not in ids:
        ids = [primary] + ids
    out: list[dict[str, Any]] = []
    for wid in ids:
        w = workplaces_by_id.get(wid)
        if w and (w.get("kind") or "").strip().lower() == "local":
            out.append(w)
    return out


def _agent_remote_workplaces(
    agent: dict[str, Any], workplaces_by_id: dict[str, dict]
) -> list[dict[str, Any]]:
    """Tunnel/SSH workplaces this agent can reach."""
    scope = (agent.get("workplace_scope") or "single").strip().lower()
    if scope == "all":
        return [
            w
            for w in workplaces_by_id.values()
            if (w.get("kind") or "").strip().lower() in ("tunnel", "ssh")
        ]
    if scope == "all_tunnels":
        return [
            w
            for w in workplaces_by_id.values()
            if (w.get("kind") or "").strip().lower() == "tunnel"
        ]
    ids: list[str] = list(agent.get("workplace_ids") or [])
    primary = (agent.get("workplace_id") or "").strip()
    if primary and primary not in ids:
        ids = [primary] + ids
    out: list[dict[str, Any]] = []
    for wid in ids:
        w = workplaces_by_id.get(wid)
        if w and (w.get("kind") or "").strip().lower() in ("tunnel", "ssh"):
            out.append(w)
    return out


def _swarm_agents_prompt_section(agent_id: str) -> str:
    """List enabled swarm members so the model can ``delegate`` by id/name.

    Injected for every agent turn that has a store (not only is_super):
    specialists still need to know peers exist if they re-delegate.
    Includes each member's workplace binding so the coordinator routes
    tunnel/SSH and specialty work to the right agents.
    """
    try:
        from app.services import store

        me = store.get_agent(agent_id)
        if not me:
            return ""
        agents = [
            a
            for a in store.list_agents()
            if a.get("enabled", True) and a.get("id")
        ]
        if not agents:
            return ""
        workplaces_by_id = {w["id"]: w for w in store.list_workplaces()}
        is_coord = bool(me.get("is_super"))
        my_wp = _agent_workplace_summary(me, workplaces_by_id)
        local_wps = _agent_local_workplaces(me, workplaces_by_id)
        remote_wps = _agent_remote_workplaces(me, workplaces_by_id)

        if is_coord:
            if local_wps:
                names = ", ".join(
                    (w.get("name") or w.get("id") or "?") for w in local_wps[:5]
                )
                access = (
                    f"Coordinator on this install. **Local** workplaces you may "
                    f"use yourself: {names} ({my_wp}). "
                    "**Tunnel/SSH work → delegate** to agents that own those "
                    "workplaces. Specialty implementation (ops/research/coding) "
                    "→ also delegate when their role fits."
                )
            else:
                access = (
                    "Coordinator on this install. **No local workplace** bound — "
                    "pure chat/planning yourself; host/file work goes to agents "
                    "with workplaces (see roster). "
                    "**Tunnel/SSH and specialty work → always delegate.**"
                )
        elif remote_wps or local_wps:
            access = (
                f"Your workplaces: {my_wp}. Run tools on those hosts yourself "
                "when the task is for you; re-delegate peers only if needed."
            )
        else:
            access = (
                f"No workplace ({my_wp}). Use tools only for non-host work, or "
                "delegate to someone who has the right workplace."
            )

        lines = [
            "## Swarm agents (live)",
            "You are **{}** (id=`{}`). {}".format(
                me.get("name") or agent_id, agent_id, access
            ),
            "Routing: **local** (this install) → Tomo/coordinator when bound; "
            "**tunnel/ssh** → agent that has that workplace; **specialty** → "
            "matching role; **swarm** → parallel `delegate` for multi-agent. "
            "Do not invent agents. Use `agent_id` or `name` from this list.",
            "",
            "Members:",
        ]
        for a in agents:
            aid = a["id"]
            name = a.get("name") or aid
            role = (a.get("role") or "").strip()
            desc = (a.get("description") or "").strip()
            wp = _agent_workplace_summary(a, workplaces_by_id)
            bits = [f"- **{name}** `id={aid}`", wp]
            if role:
                bits.append(f"role={role}")
            if a.get("is_super"):
                bits.append("coordinator/local")
            if aid == agent_id:
                bits.append("← you")
            line = " ".join(bits)
            if desc:
                # Keep roster compact for the context window.
                short = desc if len(desc) <= 120 else desc[:117] + "…"
                line += f" — {short}"
            lines.append(line)
        lines.append(
            "\nExamples: "
            'delegate(agent_id="ops", reason="On tunnel aio-serv, ping 8.8.8.8 -c 5"); '
            'delegate(agent_id="ops", reason="As Ops on local workplace sandbox-root, '
            'write /tmp/hello.txt …").'
        )
        return "\n".join(lines)
    except Exception:
        return ""


def _format_workplace_for_prompt(w: dict[str, Any]) -> str:
    """One-line workplace summary rich enough for tunnel/SSH targeting."""
    kind = (w.get("kind") or "").strip().lower() or "unknown"
    name = w.get("name") or w.get("id") or "workplace"
    wid = w.get("id") or ""
    parts = [f"- **{name}** `id={wid}` kind={kind}"]

    if kind == "tunnel":
        online = w.get("online")
        if online is True:
            parts.append("online")
        elif online is False:
            parts.append("offline")
        status = (w.get("status") or "").strip()
        if status and status not in ("connected", "offline"):
            parts.append(f"status={status}")
        hostname = (
            w.get("connector_hostname") or w.get("host") or ""
        ).strip()
        if " (" in hostname and hostname.endswith(")"):
            hostname = hostname.split(" (", 1)[0].strip()
        ip = (w.get("connector_remote_ip") or "").strip()
        if hostname:
            parts.append(f"hostname={hostname}")
        if ip and ip not in ("127.0.0.1", "::1"):
            parts.append(f"device_ip={ip}")
        plat = (w.get("connector_platform") or "").strip()
        ver = (w.get("connector_version") or "").strip()
        if "/" in ver and not plat:
            ver, plat = ver.split("/", 1)
        if plat:
            parts.append(f"platform={plat}")
        if ver:
            parts.append(f"connector={ver}")
        detail = (w.get("host_detail") or "").strip()
        if detail and detail not in (hostname, ip, "tunnel"):
            parts.append(f"detail={detail}")
    elif kind == "ssh":
        user = (w.get("ssh_user") or "").strip()
        host = (w.get("ssh_host") or "").strip()
        port = int(w.get("ssh_port") or 22)
        if user and host:
            target = f"{user}@{host}"
        else:
            target = host or user or ""
        if target:
            if port and port != 22 and host:
                target = f"{target}:{port}"
            parts.append(f"ssh={target}")
        root = (w.get("root_path") or "").strip()
        if root:
            parts.append(f"root={root}")
        status = (w.get("status") or "").strip()
        if status:
            parts.append(f"status={status}")
        if w.get("password_set"):
            parts.append("auth=password")
        elif w.get("key_set"):
            parts.append("auth=key")
    elif kind == "local":
        root = (w.get("root_path") or w.get("host") or "").strip()
        if root:
            parts.append(f"path={root}")
    else:
        detail = (w.get("host_detail") or w.get("host") or "").strip()
        if detail:
            parts.append(detail)

    return " ".join(parts)


def _workplace_prompt_section(agent_id: str) -> str:
    """Describe assigned workplaces so the model can target hosts / register paths.

    Tunnel and SSH entries include hostname, device IP, online state, and
    ssh user@host so tools can use ``workplace=<id|name|hostname>``.
    """
    try:
        from app.core import home
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
            label = "assigned workplaces" if allowed else "none"

        # Live tool cwd for this turn (session folder or ~/tomo/<agent>).
        try:
            from app.runtime.tools.sandbox import resolve_work_root
            from app.runtime.tools.workplace_ctx import (
                current_workplace_id,
                force_work_dir,
            )

            cwd = str(resolve_work_root(agent_id))
            if force_work_dir():
                cwd_line = (
                    f"**This turn's tool cwd:** `{cwd}` "
                    f"(chat = Tomo work dir `~/tomo/{agent_id}` — "
                    "not your permanently assigned local workplace)."
                )
            elif current_workplace_id():
                cwd_line = (
                    f"**This turn's tool cwd:** `{cwd}` "
                    f"(session workplace `{current_workplace_id()}`)."
                )
            else:
                cwd_line = f"**This turn's tool cwd:** `{cwd}`."
        except Exception:
            cwd_line = (
                f"**Default tool cwd (no workplace):** "
                f"`{home.agent_work_dir(agent_id)}`."
            )

        is_coord = bool(agent.get("is_super"))
        locals_ = [
            w for w in allowed if (w.get("kind") or "").strip().lower() == "local"
        ]
        remotes = [
            w
            for w in allowed
            if (w.get("kind") or "").strip().lower() in ("tunnel", "ssh")
        ]

        if is_coord:
            if locals_ and not remotes:
                status = (
                    f"**Local only** — scope={scope} ({label}). "
                    "You run tools on **local** workplaces (this Tomo install) "
                    "when the chat selects one; otherwise use Tomo work dir. "
                    "Tunnel/SSH hosts are owned by other agents — **delegate**."
                )
            elif locals_ and remotes:
                status = (
                    f"**Mixed binding** — scope={scope} ({label}). "
                    "As coordinator prefer **local** yourself; for tunnel/SSH "
                    "prefer **delegate** to specialists that own those hosts."
                )
            elif remotes and not locals_:
                status = (
                    f"**Remote binding only** — scope={scope} ({label}). "
                    "Unusual for Tomo coordinator; prefer delegating tunnel/SSH "
                    "ops to dedicated agents when possible."
                )
            else:
                status = (
                    "**No permanent workplace** — this turn uses Tomo work dir "
                    f"(`~/tomo/{agent_id}`) unless the chat picks a folder. "
                    "Host/file on other machines: **delegate**."
                )
        else:
            if allowed:
                status = (
                    f"**Connected** — scope={scope} ({label}). "
                    "Host/file/shell tools run on these workplaces; do that work "
                    "yourself when the task is for you."
                )
            else:
                status = (
                    "**No workplace** — non-host tools only, or ask coordinator "
                    "to route to an agent that has the host."
                )

        lines = [
            "## Workplaces",
            cwd_line,
            status,
            "Kinds: **local** = path on this Tomo install; **tunnel** = remote "
            "Connector (prefer online); **ssh** = Paramiko user@host.",
            "Chat folder (UI): empty = `$TOMO_WORK/<agent>` (default `~/tomo/<agent>`); "
            "picked workplace = that root for this thread. "
            "Relative paths resolve under **this turn's tool cwd**; absolute paths "
            "OK only under that root. Use list_dir to explore.",
            "Tunnel/SSH remain reachable via agents that own them, or "
            "`workplace=<id|name|hostname|ip>` on bash.",
            "To inventory hosts call **list_workplaces** (never bash find/ls "
            "under ~/tomo — workplaces are a Tomo registry, not folders).",
            "register_workplace(kind=local, path=...) binds a new **local** "
            "project path on this install.",
        ]
        if allowed:
            lines.append("Available:")
            for w in allowed[:40]:
                lines.append(_format_workplace_for_prompt(w))
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
            from app.services.chat import expand_user_content_for_llm

            messages.append(
                {"role": "user", "content": expand_user_content_for_llm(entry)}
            )
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
            # Never inject into the *target* agent — that looks like their own
            # prior assistant turn and models echo ``[Swarm] Handing off…``.
            if for_agent_id:
                to_id = (entry.get("to") or entry.get("agent_id") or "").strip()
                if to_id and to_id == for_agent_id:
                    i += 1
                    continue
                note = (entry.get("content") or "").strip() or "Handed off to specialist"
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
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    """Assemble the full message list for one agent turn.

    Layout: ``[system] + history_to_messages(history) + [user]``. The new
    ``user_message`` is appended only when provided — callers that persist
    the user entry into history first may pass ``user_message=None``.

    Pass ``for_agent_id`` so multi-agent history attributes specialist work
    (required for the coordinator to see Ops results correctly).
    When a user message is present, inject a compact retrieved-memory block
    into the system prompt (Reuse step of the learning loop).
    """
    prompt = system_prompt if system_prompt is not None else coordinator_system_prompt()
    query = (user_message or "").strip()
    if not query and history:
        for entry in reversed(history):
            if entry.get("type") == "user" and (entry.get("content") or "").strip():
                query = str(entry["content"]).strip()
                break
    if query:
        try:
            from app.runtime.memory.retrieve import retrieve_for_turn

            block = retrieve_for_turn(
                query, agent_id=for_agent_id, session_id=session_id
            )
            if block:
                prompt = f"{prompt.rstrip()}\n\n{block}"
        except Exception:
            pass
    # Always stamp a fresh clock (covers custom system_prompt callers too).
    prompt = inject_current_time(prompt)
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
    "inject_current_time",
    "freeze_prompt_clock",
    "reset_prompt_clock",
    "history_to_messages",
    "build_messages",
]
