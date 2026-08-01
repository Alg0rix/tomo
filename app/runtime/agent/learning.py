"""Active learning — observe → distill → reuse → refine.

After eligible turns, a background review may write knowledge / agent state /
artifacts / skills. The main chat turn is never blocked.

Trigger model (Hermes-style counters, not English keyword gates):

* **Memory nudge** — every N top-level successful turns (``learning_memory_nudge_turns``)
* **Skill nudge** — when this turn used enough tool calls
  (``learning_skill_nudge_iters``) or touched a skill (refine-in-place)

Better than a bare interval timer:
* Cooldown per agent (burst control)
* Nested subagents never review
* Compact tool trail (not a full message replay)
* One combined review pass when either nudge fires (cheaper than two forks)
* Frustration / correction language lives in the *review prompt only*
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from app.runtime.agent.metrics import TurnMetrics
from app.runtime.llm.base import LLMClient, LLMResponse

_logger = logging.getLogger(__name__)

_DEFAULT_MEMORY_NUDGE_TURNS = 3
_DEFAULT_SKILL_NUDGE_ITERS = 3
_COOLDOWN_SEC = 60.0
_MAX_REVIEW_ROUNDS = 4
_MAX_TRAIL_CHARS = 6_000

_REVIEW_SYSTEM = """You are Tomo's learning reviewer. You do not chat with the user.
You only distill durable knowledge from the completed turn.

You may call:
- memory — curated USER.md / MEMORY.md facts the model always sees next session
- remember — longer searchable KB documents (FTS)
- agent_state — structured key/value facts when a short key is enough
- save_artifact — catalog lasting file outputs from this turn
- list_skills / use_skill — inspect the existing skill library before writing
- manage_skill — create/patch class-level procedural skills (how to do a *type* of task)

Focus this pass:
{focus}

Signals worth acting on (any language — judge by meaning, not keywords):
- User corrected style, tone, format, verbosity, or workflow
- A non-trivial technique, fix, workaround, or debugging path emerged
- A skill loaded this turn was wrong, incomplete, or outdated — patch it
- Durable user/project facts (prefs, timezone, conventions)

Do NOT capture:
- Environment glitches ("command not found", missing packages, bad paths)
- "Tool X is broken" claims
- One-shot Q&A with no reusable procedure
- Transient errors that already resolved (capture the retry pattern instead)

Rules:
1. Prefer PATCHING an existing class-level skill over creating a narrow one-off.
2. Skill names must be class-level (e.g. python-unit-testing), never today's ticket id.
3. If genuinely nothing durable stands out, reply exactly: Nothing to save.
4. Keep skill bodies actionable (steps, pitfalls, verification). Be concise.
"""

_REVIEW_USER = """Completed turn to review.

## Goal
{goal}

## Tool trail (compact)
{trail}

## Final answer (excerpt)
{final}

## Skills touched this turn
{skills_touched}

Act with tools if warranted; otherwise say Nothing to save.
"""

# agent_id -> last review monotonic time
_last_review_at: dict[str, float] = {}
# agent_id -> successful top-level finals since last memory review
_turns_since_memory: dict[str, int] = {}


def learning_enabled() -> bool:
    try:
        from app.services import store

        return bool(store.get_settings().get("learning_enabled", True))
    except Exception:
        return True


def _setting_int(key: str, default: int) -> int:
    try:
        from app.services import store

        raw = store.get_settings().get(key, default)
        return max(1, int(raw))
    except (TypeError, ValueError, Exception):
        return default


def memory_nudge_turns() -> int:
    return _setting_int("learning_memory_nudge_turns", _DEFAULT_MEMORY_NUDGE_TURNS)


def skill_nudge_iters() -> int:
    return _setting_int("learning_skill_nudge_iters", _DEFAULT_SKILL_NUDGE_ITERS)


def decide_review(
    *,
    metrics: TurnMetrics,
    skills_touched: list[str] | None = None,
    nested: bool = False,
) -> dict[str, bool]:
    """Return ``{review_memory, review_skills}`` and advance the memory turn counter.

    Call once per successful top-level final (from ``run_learning_review``).
    """
    out = {"review_memory": False, "review_skills": False}
    if nested or not learning_enabled() or metrics.ended_kind != "final":
        return out

    key = metrics.agent_id or "_default"
    skills_touched = list(skills_touched or [])

    # Skill nudge: enough tool work this turn, or a skill was in play (refine).
    if metrics.tool_calls >= skill_nudge_iters() or skills_touched:
        out["review_skills"] = True

    # Memory nudge: every N successful top-level turns.
    _turns_since_memory[key] = _turns_since_memory.get(key, 0) + 1
    if _turns_since_memory[key] >= memory_nudge_turns():
        out["review_memory"] = True
        _turns_since_memory[key] = 0

    return out


def is_learning_eligible(
    *,
    metrics: TurnMetrics,
    user_message: str | None = None,  # call-site compat; unused (no keyword gate)
    skills_touched: list[str] | None = None,
    nested: bool = False,
) -> bool:
    """Peek whether a review would fire — does not advance turn counters."""
    _ = user_message
    if nested or not learning_enabled() or metrics.ended_kind != "final":
        return False
    skills_touched = list(skills_touched or [])
    if metrics.tool_calls >= skill_nudge_iters() or skills_touched:
        return True
    key = metrics.agent_id or "_default"
    return (_turns_since_memory.get(key, 0) + 1) >= memory_nudge_turns()


def _cooldown_ok(agent_id: str | None) -> bool:
    key = agent_id or "_default"
    last = _last_review_at.get(key, 0.0)
    return (time.monotonic() - last) >= _COOLDOWN_SEC


def _mark_reviewed(agent_id: str | None) -> None:
    _last_review_at[agent_id or "_default"] = time.monotonic()


def _focus_text(*, review_memory: bool, review_skills: bool) -> str:
    if review_memory and review_skills:
        return (
            "BOTH memory and skills. Be ACTIVE on memory: if the user stated a "
            "preference, correction, or personal detail, save it with `memory` "
            "(target=user for who they are; target=memory for env/conventions) — "
            "do not wait for them to ask. Also update class-level skills when "
            "a procedural lesson is clear."
        )
    if review_memory:
        return (
            "MEMORY primarily — be ACTIVE. Look for persona, preferences, "
            "corrections, or expectations about how you should behave. "
            "If something stands out, save with `memory` even if the user "
            "never said \"remember\". Prefer target=user for who they are. "
            "If nothing is worth saving, say 'Nothing to save.' and stop."
        )
    return (
        "SKILLS primarily — how to do this class of task. "
        "Only save memory facts if a clear durable preference/correction appeared."
    )


def compact_tool_trail(messages: list[dict[str, Any]], *, limit: int = _MAX_TRAIL_CHARS) -> str:
    """Build a compact tool trail from OpenAI-style messages."""
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role")
        if role == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
                name = fn.get("name") or "?"
                args = fn.get("arguments") or ""
                if isinstance(args, str) and len(args) > 180:
                    args = args[:180] + "…"
                lines.append(f"→ {name}({args})")
        elif role == "tool":
            name = msg.get("name") or "tool"
            content = msg.get("content") or ""
            if not isinstance(content, str):
                content = str(content)
            err = content.startswith("Error")
            snippet = content.strip().splitlines()[0][:160] if content.strip() else ""
            mark = "✗" if err else "✓"
            lines.append(f"  {mark} {name}: {snippet}")
    text = "\n".join(lines)
    if len(text) > limit:
        text = text[: limit - 20] + "\n…(truncated)"
    return text or "(no tools)"


def _learning_tool_schemas(*, review_memory: bool, review_skills: bool) -> list[dict[str, Any]]:
    from app.runtime.tools.registry import get_openai_tools

    allow: set[str] = set()
    if review_memory:
        allow.update({"remember", "agent_state", "save_artifact", "list_artifacts", "memory"})
    if review_skills:
        allow.update({"list_skills", "use_skill", "manage_skill", "save_artifact"})
    # Always allow remember for combined edge cases
    if not allow:
        allow = {"remember", "memory", "list_skills", "use_skill", "manage_skill"}

    out: list[dict[str, Any]] = []
    for schema in get_openai_tools():
        try:
            name = schema["function"]["name"]
        except (KeyError, TypeError):
            continue
        if name in allow:
            out.append(schema)
    return out


async def _run_review_llm(
    client: LLMClient,
    *,
    goal: str,
    trail: str,
    final: str,
    skills_touched: list[str],
    agent_id: str | None,
    review_memory: bool,
    review_skills: bool,
) -> dict[str, Any]:
    """Restricted tool loop; returns {saved, actions, note}."""
    from app.runtime.tools.registry import execute

    schemas = _learning_tool_schemas(
        review_memory=review_memory, review_skills=review_skills
    )
    if not schemas:
        return {"saved": False, "actions": [], "note": "no learning tools registered"}

    system = _REVIEW_SYSTEM.format(
        focus=_focus_text(review_memory=review_memory, review_skills=review_skills)
    )
    user = _REVIEW_USER.format(
        goal=(goal or "").strip() or "(empty)",
        trail=trail or "(none)",
        final=((final or "").strip()[:1500] or "(empty)"),
        skills_touched=", ".join(skills_touched) if skills_touched else "(none)",
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    actions: list[str] = []
    note = ""

    for _ in range(_MAX_REVIEW_ROUNDS):
        resp: LLMResponse = await client.complete(messages, schemas)
        if not resp.has_tool_calls:
            note = (resp.content or "").strip()
            break
        tool_calls_payload = []
        for i, call in enumerate(resp.tool_calls):
            cid = call.id or f"learn_{i}"
            tool_calls_payload.append(
                {
                    "id": cid,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments or {}),
                    },
                }
            )
        messages.append(
            {
                "role": "assistant",
                "content": resp.content or "",
                "tool_calls": tool_calls_payload,
            }
        )
        for i, call in enumerate(resp.tool_calls):
            cid = tool_calls_payload[i]["id"]
            args = dict(call.arguments or {})
            if call.name == "manage_skill" and agent_id and "agent_id" not in args:
                args["agent_id"] = agent_id
            if call.name == "agent_state" and agent_id and "agent_id" not in args:
                args["agent_id"] = agent_id
            if call.name == "memory" and agent_id and "agent_id" not in args:
                args["agent_id"] = agent_id
            result = execute(call.name, args)
            if not str(result).startswith("Error"):
                actions.append(f"{call.name}: {str(result).splitlines()[0][:120]}")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": cid,
                    "name": call.name,
                    "content": str(result)[:4000],
                }
            )
    else:
        note = note or "review round limit"

    saved = bool(actions)
    if not saved and note.lower().startswith("nothing to save"):
        note = "Nothing to save."
    return {"saved": saved, "actions": actions, "note": note}


async def run_learning_review(
    *,
    client: LLMClient,
    messages: list[dict[str, Any]],
    metrics: TurnMetrics,
    user_message: str | None,
    final_content: str,
    skills_touched: list[str] | None = None,
    nested: bool = False,
) -> dict[str, Any] | None:
    """Run a learning review when eligible. Returns summary dict or None if skipped."""
    skills_touched = list(skills_touched or [])
    flags = decide_review(
        metrics=metrics, skills_touched=skills_touched, nested=nested
    )
    if not (flags["review_memory"] or flags["review_skills"]):
        return None
    if not _cooldown_ok(metrics.agent_id):
        _logger.info("learning review skipped: cooldown agent=%s", metrics.agent_id)
        return None

    _mark_reviewed(metrics.agent_id)
    trail = compact_tool_trail(messages)
    try:
        result = await _run_review_llm(
            client,
            goal=user_message or "",
            trail=trail,
            final=final_content,
            skills_touched=skills_touched,
            agent_id=metrics.agent_id,
            review_memory=flags["review_memory"],
            review_skills=flags["review_skills"],
        )
    except Exception as exc:
        _logger.warning("learning review failed: %s", exc)
        return {"saved": False, "actions": [], "note": f"error: {exc}"}

    result["review_memory"] = flags["review_memory"]
    result["review_skills"] = flags["review_skills"]

    # Conversation memory: roll a short session summary when memory nudge fired
    # or any review ran (cheap, no extra LLM).
    if metrics.session_id and (user_message or final_content):
        try:
            from app.services import store

            snippet = (
                f"Goal: {(user_message or '').strip()[:240]}\n"
                f"Outcome: {(final_content or '').strip()[:480]}"
            )
            prev = store.get_session_summary(metrics.session_id)
            if prev and prev.get("summary"):
                snippet = (prev["summary"].strip() + "\n---\n" + snippet)[-2000:]
            store.upsert_session_summary(
                metrics.session_id,
                snippet,
                message_count=metrics.tool_calls,
            )
        except Exception as exc:
            _logger.debug("session summary update failed: %s", exc)

    if result.get("saved"):
        _logger.info(
            "learning saved agent=%s memory=%s skills=%s actions=%s",
            metrics.agent_id,
            flags["review_memory"],
            flags["review_skills"],
            result.get("actions"),
        )
    else:
        _logger.info(
            "learning idle agent=%s note=%s",
            metrics.agent_id,
            (result.get("note") or "")[:80],
        )
    return result


def schedule_learning_review(**kwargs: Any) -> None:
    """Fire-and-forget review on the running event loop (never blocks the turn)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _logger.debug("learning review skipped: no running loop")
        return

    async def _task() -> None:
        try:
            await run_learning_review(**kwargs)
        except Exception as exc:
            _logger.warning("learning background task failed: %s", exc)

    loop.create_task(_task())


def reset_learning_cooldowns() -> None:
    """Test helper — clear cooldown and turn counters."""
    _last_review_at.clear()
    _turns_since_memory.clear()


__all__ = [
    "learning_enabled",
    "memory_nudge_turns",
    "skill_nudge_iters",
    "decide_review",
    "is_learning_eligible",
    "compact_tool_trail",
    "run_learning_review",
    "schedule_learning_review",
    "reset_learning_cooldowns",
]
