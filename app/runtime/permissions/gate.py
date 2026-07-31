"""Permission gate — assess → mode → allowlist → HITL."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

from app.runtime.permissions.allowlist import (
    all_keys_approved,
    approve_permanent,
    approve_session,
)
from app.runtime.permissions.assess import assess
from app.runtime.permissions.grants import OutsideGrant
from app.runtime.permissions import messages as msg
from app.runtime.permissions.modes import get_effective_mode
from app.runtime.permissions.types import Finding

ApprovalChoice = Literal["once", "session", "always", "deny"]

HitlWaitFn = Callable[..., Awaitable[ApprovalChoice]]


@dataclass
class Decision:
    allowed: bool
    message: str | None = None
    grant: OutsideGrant = None
    findings: list[Finding] = field(default_factory=list)
    needs_hitl: bool = False
    smart_denied: bool = False
    description: str = ""
    allow_permanent: bool = True


def _deny_globs() -> list[str]:
    try:
        from app.services import store

        raw = store.get_settings().get("approvals_deny", [])
    except Exception:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw if isinstance(x, str)]
    return []


def _grant_for(findings: list[Finding], mode: str) -> OutsideGrant:
    if mode == "off":
        return "*"
    paths: list[Path] = []
    for f in findings:
        if f.kind == "escape":
            paths.extend(f.paths)
    if not paths:
        return None
    return frozenset(paths)


def _describe(findings: list[Finding]) -> str:
    if not findings:
        return "flagged action"
    return "; ".join(f.description for f in findings)


def _allowlist_keys(findings: list[Finding]) -> list[str]:
    return [f.key for f in findings if f.kind in {"escape", "dangerous"}]


def evaluate(
    tool: str,
    args: dict[str, Any] | None,
    *,
    work_root: Path,
    session_id: str | None = None,
) -> Decision:
    """Sync assessment up to HITL (does not wait)."""
    arguments = args if isinstance(args, dict) else {}
    assessment = assess(tool, arguments, work_root, deny_globs=_deny_globs())
    findings = list(assessment.findings)
    mode = get_effective_mode(session_id)

    if assessment.has_hardline():
        hard = next(f for f in findings if f.kind == "hardline")
        return Decision(
            allowed=False,
            message=msg.hardline_blocked(hard.description),
            findings=findings,
            description=hard.description,
        )

    if assessment.has_user_deny():
        deny = next(f for f in findings if f.kind == "user_deny")
        return Decision(
            allowed=False,
            message=msg.user_deny_blocked(deny.description),
            findings=findings,
            description=deny.description,
        )

    if not findings:
        return Decision(allowed=True, grant=None, findings=[])

    if mode == "off":
        return Decision(
            allowed=True,
            grant=_grant_for(findings, mode),
            findings=findings,
            description=_describe(findings),
        )

    keys = _allowlist_keys(findings)
    if keys and all_keys_approved(session_id, keys):
        return Decision(
            allowed=True,
            grant=_grant_for(findings, mode),
            findings=findings,
            description=_describe(findings),
        )

    description = _describe(findings)
    has_permanent = any(f.kind in {"dangerous", "escape"} for f in findings)
    return Decision(
        allowed=False,
        needs_hitl=True,
        findings=findings,
        description=description,
        allow_permanent=has_permanent,
        message=msg.approval_required_no_waiter(description),
    )


def apply_choice(
    decision: Decision,
    choice: ApprovalChoice,
    *,
    session_id: str | None,
    timed_out: bool = False,
) -> Decision:
    """Apply once/session/always/deny to a ``needs_hitl`` decision."""
    mode = get_effective_mode(session_id)
    description = decision.description or _describe(decision.findings)
    if choice == "deny" or timed_out:
        return Decision(
            allowed=False,
            message=msg.consent_denied(description, timed_out=timed_out),
            findings=decision.findings,
            description=description,
            smart_denied=decision.smart_denied,
        )
    keys = _allowlist_keys(decision.findings)
    if choice == "session" and session_id:
        for key in keys:
            approve_session(session_id, key)
    elif choice == "always":
        for key in keys:
            if session_id:
                approve_session(session_id, key)
            approve_permanent(key)
    return Decision(
        allowed=True,
        grant=_grant_for(decision.findings, mode),
        findings=decision.findings,
        description=description,
    )


async def decide(
    tool: str,
    args: dict[str, Any] | None,
    *,
    work_root: Path,
    session_id: str | None = None,
    hitl_wait: HitlWaitFn | None = None,
) -> Decision:
    """Full pipeline; optional ``hitl_wait`` for automated tests."""
    decision = evaluate(
        tool, args, work_root=work_root, session_id=session_id
    )
    if not decision.needs_hitl:
        return decision
    if hitl_wait is None:
        return decision
    choice = await hitl_wait(
        tool=tool,
        args=args if isinstance(args, dict) else {},
        findings=decision.findings,
        description=decision.description,
        allow_permanent=decision.allow_permanent,
        smart_denied=decision.smart_denied,
        session_id=session_id,
    )
    return apply_choice(decision, choice, session_id=session_id)


__all__ = [
    "Decision",
    "decide",
    "evaluate",
    "apply_choice",
    "ApprovalChoice",
    "HitlWaitFn",
]
