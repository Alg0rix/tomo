"""LLM session auto-title — provisional upgrade after the first reply.

The store sets a provisional title from the first user message. After the
first assistant ``final``, :func:`generate_session_title` asks the configured
LLM for a short name. Failures return ``None`` so callers keep the provisional
title and never fail the chat turn.
"""

from __future__ import annotations

import logging
from typing import Any

from app.models.mixins.messages import derive_session_title
from app.runtime.llm.base import LLMClient

logger = logging.getLogger(__name__)

_TITLE_MAX_LEN = 60
_SNIPPET_MAX = 500

_SYSTEM = (
    "You name chat sessions. Reply with a short title only (3-6 words). "
    "No quotes, no trailing punctuation, no markdown, plain text only."
)


def sanitize_llm_title(raw: str, *, max_len: int = _TITLE_MAX_LEN) -> str | None:
    """Normalize model output into a session title, or ``None`` if unusable."""
    text = (raw or "").strip()
    if not text:
        return None
    # Drop wrapping quotes / backticks common in model replies.
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"`":
        text = text[1:-1].strip()
    text = " ".join(text.split()).strip().strip("\"'`")
    if not text:
        return None
    # First line only if the model rambling-newline'd.
    text = text.split("\n", 1)[0].strip()
    if not text:
        return None
    return derive_session_title(text, max_len=max_len)


def first_user_and_final(history: list[dict[str, Any]]) -> tuple[str, str] | None:
    """Return ``(first_user, first_final)`` content, or ``None`` if incomplete."""
    user: str | None = None
    final: str | None = None
    for entry in history:
        t = entry.get("type")
        if t == "user" and user is None:
            user = str(entry.get("content") or "")
        elif t == "final" and final is None:
            final = str(entry.get("content") or "")
        if user is not None and final is not None:
            return user, final
    return None


def llm_title_skip_reason(
    session: dict[str, Any] | None, history: list[dict[str, Any]]
) -> str | None:
    """Return why LLM title should be skipped, or ``None`` when eligible."""
    if not session:
        return "no session"
    users = [e for e in history if e.get("type") == "user"]
    finals = [e for e in history if e.get("type") == "final"]
    if len(users) != 1:
        return f"user_count={len(users)} (need 1)"
    if not finals:
        return "no final reply yet"
    first = str(users[0].get("content") or "")
    provisional = derive_session_title(first)
    current = session.get("title") or ""
    if current != provisional:
        return f"title already set title={current!r} provisional={provisional!r}"
    return None


def should_llm_title(
    session: dict[str, Any] | None, history: list[dict[str, Any]]
) -> bool:
    """True when this session is eligible for a one-shot LLM title upgrade."""
    return llm_title_skip_reason(session, history) is None


async def generate_session_title(
    user_text: str,
    assistant_text: str,
    *,
    llm: LLMClient | None = None,
) -> str | None:
    """Ask the LLM for a short title; return sanitized text or ``None``."""
    try:
        client = llm
        if client is None:
            from app.runtime.llm import get_llm

            client = get_llm()
        user_snip = " ".join((user_text or "").split())[:_SNIPPET_MAX]
        asst_snip = " ".join((assistant_text or "").split())[:_SNIPPET_MAX]
        logger.info(
            "session title LLM request user_chars=%d asst_chars=%d",
            len(user_snip),
            len(asst_snip),
        )
        messages = [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": (
                    f"User message:\n{user_snip}\n\n"
                    f"Assistant reply:\n{asst_snip}\n\n"
                    "Title:"
                ),
            },
        ]
        resp = await client.complete(messages, tools=None)
        raw = (resp.content or "").strip()
        title = sanitize_llm_title(raw)
        logger.info(
            "session title LLM response raw=%r sanitized=%r",
            raw[:120],
            title,
        )
        if not title:
            logger.warning("session title discarded after sanitize (empty or unusable)")
        return title
    except Exception as exc:
        logger.warning("session title generation failed: %s", exc, exc_info=True)
        return None


__all__ = [
    "sanitize_llm_title",
    "first_user_and_final",
    "llm_title_skip_reason",
    "should_llm_title",
    "generate_session_title",
]
