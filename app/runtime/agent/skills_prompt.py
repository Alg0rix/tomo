"""Skill awareness + behavioral guidance for the system prompt.

Progressive disclosure: inject a compact name/description catalog so the model
knows what exists; full bodies load on demand via ``use_skill`` (or slash).
Tool-gated guidance (scan / load / distill) lives here — not in SYSTEM.md.
"""

from __future__ import annotations

from typing import Any

# Short index lines with a hard cap on catalog size.
_SKILL_DESC_LIMIT = 80
_SKILL_CATALOG_CAP = 48
_SKILL_TOOLS = frozenset({"list_skills", "use_skill", "manage_skill"})

# Injected when manage_skill is enabled.
SKILLS_GUIDANCE = (
    "After completing a complex task (several tool calls), fixing a tricky "
    "error, or discovering a reusable workflow, distill it with "
    "`manage_skill` so you can reuse it next time. Prefer `list_skills` / "
    "`use_skill` before reinventing a procedure. When a skill references "
    "`references/` (or templates/scripts/assets), load them with "
    "`use_skill(skill_id=…, file=references/…)` — do not `read_file` absolute "
    "paths under ~/.agents, ~/.tomo, or ~/.claude (outside the workplace). "
    "Do not save one-off chatter or \"tool X is broken\" as a skill."
)


def _truncate_desc(text: str, limit: int = _SKILL_DESC_LIMIT) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "…"


def _enabled_skill_tools(agent_id: str) -> set[str]:
    from app.services import store

    return store.get_enabled_tool_ids(agent_id) & _SKILL_TOOLS


def _catalog_rows(agent_id: str, user_id: str | None = None) -> tuple[list[dict[str, Any]], set[str], set[str]]:
    """Return (ordered enabled skills, assigned ids, user-pinned ids).

    Assigned skills first, then this user's own pins (their per-user
    activation overlay — shared ``agent_skills`` rows are never written),
    then the rest. ``user_id=None`` preserves the shared-config view.
    """
    from app.services import store

    from app.extensions.skills import is_runtime_only_skill

    store.sync_skills()
    if user_id:
        rows = store.get_effective_agent_skills(agent_id, user_id)
    else:
        rows = store.get_agent_skills(agent_id)
    enabled = [s for s in rows if s.get("enabled", True)
               and not is_runtime_only_skill(s.get("id"), s.get("source"))]
    assigned_ids = {s["id"] for s in enabled if s.get("assigned")}
    pinned_ids = {s["id"] for s in enabled if s.get("user_activated")}
    assigned = [s for s in enabled if s["id"] in assigned_ids]
    pinned = [s for s in enabled if s["id"] in pinned_ids]
    others = [s for s in enabled if s["id"] not in assigned_ids and s["id"] not in pinned_ids]
    return assigned + pinned + others, assigned_ids, pinned_ids


def build_skills_system_prompt(agent_id: str | None, user_id: str | None = None) -> str:
    """``## Skills`` section (catalog + guidance), or ``""`` when tools unavailable.

    Injected for every agent that has ``list_skills`` / ``use_skill`` /
    ``manage_skill``. Bodies are never inlined — the model must call
    ``use_skill``. Guidance is included even when the catalog is empty.
    With ``user_id``, the catalog merges that user's own activation pins
    (marked ``+``); shared assignment (``*``) is never altered here.
    """
    if not agent_id:
        return ""
    try:
        skill_tools = _enabled_skill_tools(agent_id)
        if not skill_tools:
            return ""
        ordered, assigned_ids, pinned_ids = _catalog_rows(agent_id, user_id)
    except Exception:
        return ""

    parts: list[str] = [
        "## Skills",
        "Before non-trivial work, scan available skills. If one matches or is "
        "partially relevant, call use_skill(skill_id=...) and follow its "
        "instructions.",
    ]

    if ordered:
        if assigned_ids:
            parts.append("Skills marked * are assigned to you.")
        if pinned_ids:
            parts.append("Skills marked + are your own pins for this turn.")
        parts.append("<available_skills>")
        shown = ordered[:_SKILL_CATALOG_CAP]
        for skill in shown:
            sid = skill.get("id") or ""
            if not sid:
                continue
            desc = _truncate_desc(
                str(skill.get("description") or skill.get("name") or sid)
            )
            mark = ("*" if sid in assigned_ids else "") + ("+" if sid in pinned_ids else "")
            parts.append(f"- {sid}{mark}: {desc}")
        parts.append("</available_skills>")
        omitted = len(ordered) - len(shown)
        if omitted > 0:
            parts.append(
                f"… +{omitted} more (call list_skills for the full catalog)."
            )
        parts.append(
            "Only proceed without loading a skill if genuinely none are relevant."
        )
    else:
        parts.append(
            "No skills are installed yet. Call list_skills to confirm, or "
            "manage_skill to create one after non-trivial work."
        )

    if "manage_skill" in skill_tools:
        parts.append(SKILLS_GUIDANCE)

    return "\n".join(parts)
