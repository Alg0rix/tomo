"""Model-facing BLOCKED / consent messages."""

from __future__ import annotations


def hardline_blocked(description: str) -> str:
    return (
        f"BLOCKED (hardline): {description}. "
        "This command is on the unconditional blocklist and cannot be executed "
        "via the agent — not even with /auto or approvals mode off. If you "
        "genuinely need to run it, run it yourself outside the agent."
    )


def user_deny_blocked(description: str) -> str:
    return (
        f"BLOCKED: {description}. It cannot be executed via the agent — not "
        "even with /auto. Do NOT retry or rephrase this command."
    )


def consent_denied(description: str, *, timed_out: bool = False) -> str:
    if timed_out:
        reason = "timed out without user response"
        addendum = " Silence is not consent."
    else:
        reason = "denied by user"
        addendum = ""
    return (
        f"BLOCKED: Action {reason} ({description}). The user has NOT consented "
        "to this action. Do NOT retry it, do NOT rephrase it, and do NOT "
        f"attempt the same outcome via a different path.{addendum}"
    )


def approval_required_no_waiter(description: str) -> str:
    return (
        f"BLOCKED: approval required ({description}) but no interactive "
        "approval UI is available. Do NOT retry."
    )


def smart_denied(description: str) -> str:
    return (
        f"BLOCKED by smart approval: {description}. "
        "The command was assessed as genuinely dangerous. Do NOT retry."
    )


__all__ = [
    "hardline_blocked",
    "user_deny_blocked",
    "consent_denied",
    "approval_required_no_waiter",
    "smart_denied",
]
