"""Async human-in-the-loop waiters for approvals and clarify."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

ApprovalChoice = Literal["once", "session", "always", "deny"]

_DEFAULT_TIMEOUT = 300.0


@dataclass
class _PendingApproval:
    id: str
    session_id: str | None
    event: asyncio.Event = field(default_factory=asyncio.Event)
    payload: dict[str, Any] = field(default_factory=dict)
    choice: ApprovalChoice | None = None
    reason: str | None = None
    created_at: float = field(default_factory=time.monotonic)


@dataclass
class _PendingClarify:
    id: str
    session_id: str | None
    event: asyncio.Event = field(default_factory=asyncio.Event)
    payload: dict[str, Any] = field(default_factory=dict)
    answer: str | None = None
    created_at: float = field(default_factory=time.monotonic)


_approvals: dict[str, _PendingApproval] = {}
_clarifies: dict[str, _PendingClarify] = {}


def _timeout_seconds() -> float:
    try:
        from app.services import store

        raw = store.get_settings().get("approvals_timeout", _DEFAULT_TIMEOUT)
        return max(5.0, float(raw))
    except Exception:
        return _DEFAULT_TIMEOUT


def _findings_payload(findings: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in findings:
        out.append(
            {
                "kind": getattr(f, "kind", None),
                "key": getattr(f, "key", None),
                "description": getattr(f, "description", str(f)),
            }
        )
    return out


def create_approval(
    *,
    tool: str,
    args: dict[str, Any],
    findings: list[Any],
    description: str,
    allow_permanent: bool = True,
    smart_denied: bool = False,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Register a pending approval; return SSE payload (includes ``id``)."""
    pid = uuid.uuid4().hex
    preview = args
    if isinstance(args, dict):
        preview = {k: v for k, v in args.items() if k != "env"}
    if smart_denied:
        choices = ["once", "deny"]
    elif allow_permanent:
        choices = ["once", "session", "always", "deny"]
    else:
        choices = ["once", "session", "deny"]
    payload = {
        "id": pid,
        "kind": "approval",
        "tool": tool,
        "args_preview": preview,
        "findings": _findings_payload(findings),
        "description": description,
        "allow_permanent": allow_permanent and not smart_denied,
        "allow_session": not smart_denied,
        "smart_denied": smart_denied,
        "session_id": session_id,
        "choices": choices,
    }
    _approvals[pid] = _PendingApproval(
        id=pid, session_id=session_id, payload=payload
    )
    return payload


async def await_approval(
    approval_id: str,
    timeout: float | None = None,
) -> ApprovalChoice:
    pending = _approvals.get(approval_id)
    if pending is None:
        return "deny"
    to = timeout if timeout is not None else _timeout_seconds()
    try:
        try:
            await asyncio.wait_for(pending.event.wait(), timeout=to)
        except asyncio.TimeoutError:
            return "deny"
        return pending.choice or "deny"
    finally:
        _approvals.pop(approval_id, None)


async def request_approval(
    *,
    tool: str,
    args: dict[str, Any],
    findings: list[Any],
    description: str,
    allow_permanent: bool = True,
    smart_denied: bool = False,
    session_id: str | None = None,
    timeout: float | None = None,
) -> ApprovalChoice:
    """Create + wait (for tests / callers that do not need to yield SSE first)."""
    payload = create_approval(
        tool=tool,
        args=args,
        findings=findings,
        description=description,
        allow_permanent=allow_permanent,
        smart_denied=smart_denied,
        session_id=session_id,
    )
    return await await_approval(payload["id"], timeout=timeout)


def resolve_approval(
    approval_id: str,
    choice: str,
    reason: str | None = None,
) -> None:
    pending = _approvals.get(approval_id)
    if pending is None:
        raise KeyError(approval_id)
    if pending.event.is_set():
        raise RuntimeError("already resolved")
    normalized = (choice or "").strip().lower()
    if normalized not in {"once", "session", "always", "deny"}:
        raise ValueError(f"invalid choice: {choice}")
    if pending.payload.get("smart_denied") and normalized in {"session", "always"}:
        normalized = "once"
    pending.choice = normalized  # type: ignore[assignment]
    pending.reason = reason
    pending.event.set()


def create_clarify(
    *,
    question: str,
    choices: list[str] | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    pid = uuid.uuid4().hex
    cleaned: list[str] = []
    for c in choices or []:
        if isinstance(c, str) and c.strip() and len(cleaned) < 4:
            cleaned.append(c.strip())
    payload = {
        "id": pid,
        "kind": "clarify",
        "question": question,
        "choices": cleaned,
        "session_id": session_id,
    }
    _clarifies[pid] = _PendingClarify(
        id=pid, session_id=session_id, payload=payload
    )
    return payload


async def await_clarify(
    clarify_id: str,
    timeout: float | None = None,
) -> str:
    pending = _clarifies.get(clarify_id)
    if pending is None:
        return ""
    to = timeout if timeout is not None else _timeout_seconds()
    try:
        try:
            await asyncio.wait_for(pending.event.wait(), timeout=to)
        except asyncio.TimeoutError:
            return ""
        return pending.answer if isinstance(pending.answer, str) else ""
    finally:
        _clarifies.pop(clarify_id, None)


async def request_clarify(
    *,
    question: str,
    choices: list[str] | None = None,
    session_id: str | None = None,
    timeout: float | None = None,
) -> str:
    payload = create_clarify(
        question=question, choices=choices, session_id=session_id
    )
    return await await_clarify(payload["id"], timeout=timeout)


def resolve_clarify(clarify_id: str, answer: str) -> None:
    pending = _clarifies.get(clarify_id)
    if pending is None:
        raise KeyError(clarify_id)
    if pending.event.is_set():
        raise RuntimeError("already resolved")
    pending.answer = answer if isinstance(answer, str) else str(answer)
    pending.event.set()


def clear_all_pending() -> None:
    for p in list(_approvals.values()):
        p.choice = "deny"
        p.event.set()
    for p in list(_clarifies.values()):
        p.answer = ""
        p.event.set()
    _approvals.clear()
    _clarifies.clear()


__all__ = [
    "create_approval",
    "await_approval",
    "request_approval",
    "resolve_approval",
    "create_clarify",
    "await_clarify",
    "request_clarify",
    "resolve_clarify",
    "clear_all_pending",
    "ApprovalChoice",
]
