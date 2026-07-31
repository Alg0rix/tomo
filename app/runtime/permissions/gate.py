"""Permission gate — assess → mode → allowlist → HITL."""

from __future__ import annotations

from dataclasses import dataclass
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
from app.runtime.permissions.types import Assessment, Finding

ApprovalChoice = Literal["once", "session", "always", "deny"]

HitlWaitFn = Callable[..., Awaitable[ApprovalChoice]]


@dataclass
class Decision:
    allowed: bool
    message: str | None = None
    grant: OutsideGrant = None
    findings: list[Finding] | None = None
    smart_denied: bool = False
    # Populated by loop layer when emitting SSE before await completes
    approval_event: dict[str, Any] | None = None


def _deny_globs() -> list[str]:
    try:
        from app.services import store

        raw = store.get_settings().get("approvals_deny", [])
    except Exception:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw if isinstance(x, str)]
    return []


def _grant_for(assessment: Assessment, mode: str) -> OutsideGrant:
    if mode == "off":
        return "*"
    paths = assessment.escape_paths()
    if not paths:
        return None
    return frozenset(paths)


def _describe(assessment: Assessment) -> str:
    if not assessment.findings:
        return "flagged action"
    return "; ".join(f.description for f in assessment.findings)


async def decide(
    tool: str,
    args: dict[str, Any] | None,
    *,
    work_root: Path,
    session_id: str | None = None,
    hitl_wait: HitlWaitFn | None = None,
) -> Decision:
    """Run the approval pipeline for one tool call."""
    arguments = args if isinstance(args, dict) else {}
    assessment = assess(tool, arguments, work_root, deny_globs=_deny_globs())
    mode = get_effective_mode(session_id)

    if assessment.has_hardline():
        hard = next(f for f in assessment.findings if f.kind == "hardline")
        return Decision(
            allowed=False,
            message=msg.hardline_blocked(hard.description),
            findings=list(assessment.findings),
        )

    if assessment.has_user_deny():
        deny = next(f for f in assessment.findings if f.kind == "user_deny")
        return Decision(
            allowed=False,
            message=msg.user_deny_blocked(deny.description),
            findings=list(assessment.findings),
        )

    if not assessment.findings:
        return Decision(allowed=True, grant=None, findings=[])

    if mode == "off":
        return Decision(
            allowed=True,
            grant=_grant_for(assessment, mode),
            findings=list(assessment.findings),
        )

    keys = assessment.allowlist_keys()
    if keys and all_keys_approved(session_id, keys):
        return Decision(
            allowed=True,
            grant=_grant_for(assessment, mode),
            findings=list(assessment.findings),
        )

    description = _describe(assessment)
    # smart mode handled in Task 8 — for now treat like manual escalate
    if hitl_wait is None:
        return Decision(
            allowed=False,
            message=msg.approval_required_no_waiter(description),
            findings=list(assessment.findings),
        )

    has_permanent = any(f.kind == "dangerous" for f in assessment.findings) or any(
        f.kind == "escape" for f in assessment.findings
    )
    choice = await hitl_wait(
        tool=tool,
        args=arguments,
        findings=assessment.findings,
        description=description,
        allow_permanent=has_permanent,
        smart_denied=False,
        session_id=session_id,
    )
    if choice == "deny":
        return Decision(
            allowed=False,
            message=msg.consent_denied(description),
            findings=list(assessment.findings),
        )
    if choice == "session" and session_id:
        for key in keys:
            approve_session(session_id, key)
    elif choice == "always":
        if session_id:
            for key in keys:
                approve_session(session_id, key)
                approve_permanent(key)
        else:
            for key in keys:
                approve_permanent(key)
    # once: no persistence
    return Decision(
        allowed=True,
        grant=_grant_for(assessment, mode),
        findings=list(assessment.findings),
    )


__all__ = ["Decision", "decide", "ApprovalChoice", "HitlWaitFn"]
