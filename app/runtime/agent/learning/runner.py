"""Background learning review runner — isolated tool loop."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import contextmanager
from typing import Any, Iterator

from app.runtime.agent.learning.digest import build_review_digest
from app.runtime.agent.learning.prompts import system_prompt
from app.runtime.agent.learning.state import (
    ReviewPlan,
    begin_review,
    enter_review_scope,
    exit_review_scope,
    finish_review,
    hydrate_from_session,
    observe_turn,
)
from app.runtime.agent.metrics import TurnMetrics
from app.runtime.llm.base import LLMClient, LLMResponse

_logger = logging.getLogger(__name__)

_MAX_REVIEW_ROUNDS = 5


@contextmanager
def _review_isolation(agent_id: str | None) -> Iterator[None]:
    """Bind agent sandbox + block nested observe_turn during the review."""
    from app.runtime.tools.sandbox import bind_agent, reset_agent

    scope_token = enter_review_scope()
    try:
        agent_token = bind_agent(agent_id)
    except Exception as exc:
        exit_review_scope(scope_token)
        _logger.warning("learning review sandbox bind failed: %s", exc)
        raise
    try:
        yield
    finally:
        try:
            reset_agent(agent_token)
        except Exception:
            _logger.debug("sandbox reset failed", exc_info=True)
        exit_review_scope(scope_token)

_ALLOWED_REVIEW_TOOLS = frozenset(
    {
        "remember",
        "memory",
        "agent_state",
        "list_skills",
        "use_skill",
        "manage_skill",
        "list_artifacts",
        "save_artifact",
    }
)


def _resolve_review_client(fallback: LLMClient) -> tuple[LLMClient, bool]:
    """Optionally route review to a dedicated cheaper profile.

    Settings:
      learning_review_profile_id — LLM profile id (empty = use turn client)
    Returns (client, routed).
    """
    try:
        from app.services import store

        pid = (store.get_settings().get("learning_review_profile_id") or "").strip()
        if not pid:
            return fallback, False
        profile = store.get_llm_profile(pid)
        if not profile or not profile.get("enabled", True):
            return fallback, False
        from app.runtime.llm.openai_compat import OpenAICompatClient

        base_url = (profile.get("base_url") or "").strip() or "https://api.openai.com/v1"
        model = (profile.get("model") or "").strip() or "gpt-4o-mini"
        key = profile.get("api_key") or ""
        if not key:
            return fallback, False
        return (
            OpenAICompatClient(base_url=base_url, api_key=key, model=model),
            True,
        )
    except Exception as exc:
        _logger.debug("learning review client route failed: %s", exc)
        return fallback, False


def _learning_tool_schemas(*, review_memory: bool, review_skills: bool) -> list[dict[str, Any]]:
    from app.runtime.tools.registry import get_openai_tools

    allow: set[str] = set()
    if review_memory:
        allow.update(
            {
                "remember",
                "agent_state",
                "save_artifact",
                "list_artifacts",
                "memory",
            }
        )
    if review_skills:
        allow.update(
            {
                "list_skills",
                "use_skill",
                "manage_skill",
                "save_artifact",
            }
        )
    if not allow:
        allow = {
            "remember",
            "memory",
            "list_skills",
            "use_skill",
            "manage_skill",
        }

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
    digest: str,
    agent_id: str | None,
    review_memory: bool,
    review_skills: bool,
) -> dict[str, Any]:
    from app.runtime.tools.registry import execute

    schemas = _learning_tool_schemas(
        review_memory=review_memory, review_skills=review_skills
    )
    if not schemas:
        return {"saved": False, "actions": [], "note": "no learning tools registered"}

    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": system_prompt(
                review_memory=review_memory, review_skills=review_skills
            ),
        },
        {"role": "user", "content": digest},
    ]
    actions: list[str] = []
    note = ""
    loop = asyncio.get_running_loop()

    with _review_isolation(agent_id):
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
                if call.name not in _ALLOWED_REVIEW_TOOLS:
                    result = f"Error: tool '{call.name}' is not allowed in learning review"
                else:
                    if call.name in {"manage_skill", "agent_state", "memory"} and agent_id:
                        if "agent_id" not in args:
                            args["agent_id"] = agent_id
                    try:
                        result = await loop.run_in_executor(
                            None, execute, call.name, args
                        )
                    except Exception as exc:  # pragma: no cover
                        result = f"Error: {exc}"
                if not str(result).startswith("Error"):
                    actions.append(f"{call.name}: {str(result).splitlines()[0][:140]}")
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


def _update_session_summary(
    session_id: str | None,
    user_message: str | None,
    final_content: str,
    tool_calls: int,
) -> None:
    if not session_id or not (user_message or final_content):
        return
    try:
        from app.services import store

        snippet = (
            f"Goal: {(user_message or '').strip()[:240]}\n"
            f"Outcome: {(final_content or '').strip()[:480]}"
        )
        prev = store.get_session_summary(session_id)
        if prev and prev.get("summary"):
            snippet = (prev["summary"].strip() + "\n---\n" + snippet)[-2000:]
        store.upsert_session_summary(
            session_id,
            snippet,
            message_count=tool_calls,
        )
    except Exception as exc:
        _logger.debug("session summary update failed: %s", exc)


async def run_learning_review(
    *,
    client: LLMClient,
    messages: list[dict[str, Any]],
    metrics: TurnMetrics,
    user_message: str | None,
    final_content: str,
    skills_touched: list[str] | None = None,
    nested: bool = False,
    plan: ReviewPlan | None = None,
) -> dict[str, Any] | None:
    """Run a learning review when eligible. Returns summary dict or None if skipped."""
    skills_touched = list(skills_touched or [])

    if plan is None:
        plan = observe_turn(
            agent_id=metrics.agent_id,
            tool_calls=metrics.tool_calls,
            skills_touched=skills_touched,
            nested=nested,
            ended_kind=metrics.ended_kind,
        )
    if plan is None or not plan.any:
        return None
    if not begin_review(plan):
        _logger.info(
            "learning review deferred agent=%s reason=claim_failed plan=%s",
            metrics.agent_id,
            plan.reason,
        )
        return None

    review_client, routed = _resolve_review_client(client)
    digest = build_review_digest(
        messages=messages,
        user_message=user_message,
        final_content=final_content,
        skills_touched=skills_touched or plan.skills_touched,
        tool_calls=metrics.tool_calls,
        plan_reason=plan.reason,
    )

    try:
        result = await _run_review_llm(
            review_client,
            digest=digest,
            agent_id=metrics.agent_id,
            review_memory=plan.review_memory,
            review_skills=plan.review_skills,
        )
    except Exception as exc:
        _logger.warning("learning review failed: %s", exc)
        finish_review(metrics.agent_id, saved=False)
        return {
            "saved": False,
            "actions": [],
            "note": f"error: {exc}",
            "review_memory": plan.review_memory,
            "review_skills": plan.review_skills,
            "reason": plan.reason,
            "routed": routed,
        }

    finish_review(metrics.agent_id, saved=bool(result.get("saved")))
    result["review_memory"] = plan.review_memory
    result["review_skills"] = plan.review_skills
    result["reason"] = plan.reason
    result["routed"] = routed
    result["plan"] = plan.as_dict()

    _update_session_summary(
        metrics.session_id,
        user_message,
        final_content,
        metrics.tool_calls,
    )

    if result.get("saved"):
        _logger.info(
            "learning saved agent=%s memory=%s skills=%s routed=%s reason=%s actions=%s",
            metrics.agent_id,
            plan.review_memory,
            plan.review_skills,
            routed,
            plan.reason,
            result.get("actions"),
        )
    else:
        _logger.info(
            "learning idle agent=%s reason=%s note=%s",
            metrics.agent_id,
            plan.reason,
            (result.get("note") or "")[:100],
        )
    return result


def schedule_learning_review(**kwargs: Any) -> None:
    """Fire-and-forget review on the running event loop (never blocks the turn)."""
    # Hydrate counters once per session before observing (inside the task so
    # we don't block the turn path on DB reads more than necessary — actually
    # hydrate is cheap; do it sync before spawn so observe sees seeded state).
    metrics = kwargs.get("metrics")
    if isinstance(metrics, TurnMetrics):
        hydrate_from_session(metrics.session_id, metrics.agent_id)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _logger.debug("learning review skipped: no running loop")
        return

    # Observe on the main path so counters advance even if the task is delayed.
    plan = observe_turn(
        agent_id=metrics.agent_id if isinstance(metrics, TurnMetrics) else None,
        tool_calls=metrics.tool_calls if isinstance(metrics, TurnMetrics) else 0,
        skills_touched=kwargs.get("skills_touched"),
        nested=bool(kwargs.get("nested")),
        ended_kind=metrics.ended_kind if isinstance(metrics, TurnMetrics) else "final",
    )
    if plan is None:
        return

    async def _task() -> None:
        try:
            await run_learning_review(plan=plan, **kwargs)
        except Exception as exc:
            _logger.warning("learning background task failed: %s", exc)
            if isinstance(metrics, TurnMetrics):
                finish_review(metrics.agent_id, saved=False)

    loop.create_task(_task())


__all__ = [
    "run_learning_review",
    "schedule_learning_review",
]
