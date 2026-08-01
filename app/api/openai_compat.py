"""OpenAI-compatible chat completions for API integrations.

``POST /v1/chat/completions`` — Hermes-style adapter over Tomo session turns.
``model`` is a Tomo agent id (solo auto-session). Pass ``X-Tomo-Session-Id`` to
continue an existing session (solo or swarm); history lives in the session DB.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.core.deps import AuthDep, session_user_id
from app.services import run_session_turn, store

router = APIRouter(prefix="/v1", tags=["openai-compat"])

_SESSION_HEADER = "X-Tomo-Session-Id"
_UNSAFE_SESSION_ID = re.compile(r"[\r\n\x00/\\]")


class ChatCompletionsIn(BaseModel):
    model: str = Field(min_length=1)
    messages: list[dict[str, Any]] = Field(min_length=1)
    stream: bool = False


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
        return "\n".join(p for p in parts if p)
    return str(content)


def last_user_message(messages: list[dict[str, Any]]) -> str:
    """Return text of the last ``user`` message in an OpenAI messages list."""
    for msg in reversed(messages):
        if (msg.get("role") or "") != "user":
            continue
        text = _content_text(msg.get("content")).strip()
        if text:
            return text
    return ""


def parse_sse_block(raw: str) -> tuple[str | None, dict[str, Any]]:
    """Parse one Tomo SSE frame into ``(event_name, data)``."""
    name: str | None = None
    data_lines: list[str] = []
    for line in raw.splitlines():
        if line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if not data_lines:
        return name, {}
    payload = "\n".join(data_lines)
    try:
        data = json.loads(payload) if payload else {}
    except json.JSONDecodeError:
        data = {"raw": payload}
    if not isinstance(data, dict):
        data = {"value": data}
    return name, data


def _openai_error(message: str, *, err_type: str = "invalid_request_error") -> dict[str, Any]:
    return {"error": {"message": message, "type": err_type}}


def resolve_session_id(
    request: Request, *, agent_id: str, user_id: str
) -> tuple[str | None, dict[str, Any] | None]:
    """Resolve session from ``X-Tomo-Session-Id`` or create a solo agent session.

    Returns ``(session_id, error_body)``. On success ``error_body`` is None.
    """
    provided = (request.headers.get(_SESSION_HEADER) or "").strip()
    if provided:
        if _UNSAFE_SESSION_ID.search(provided):
            return None, _openai_error("Invalid session ID")
        if not store.get_session(provided):
            return None, _openai_error(
                f"Session not found: {provided}",
                err_type="not_found_error",
            )
        return provided, None
    return store.get_or_create_session(agent_id, user_id), None


def _completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


def _chunk(
    completion_id: str,
    created: int,
    model: str,
    delta: dict[str, Any],
    finish_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }


def _sse_data(payload: dict[str, Any] | str) -> str:
    if isinstance(payload, str):
        return f"data: {payload}\n\n"
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"


def _response_model(agent_id: str, session_id: str) -> str:
    """OpenAI ``model`` field: prefer requested agent id, else session coordinator."""
    if store.get_agent(agent_id):
        return agent_id
    session = store.get_session(session_id) or {}
    return (
        session.get("coordinator_id")
        or session.get("agent_id")
        or (session.get("agent_ids") or ["main"])[0]
    )


async def _collect_assistant_text(
    session_id: str, message: str, user_id: str
) -> tuple[str, bool]:
    """Drain a turn; return ``(assistant_text, had_error)``."""
    text_parts: list[str] = []
    done_text = ""
    had_error = False
    async for raw in run_session_turn(session_id, message, user_id, start_seq=0):
        for block in raw.split("\n\n"):
            block = block.strip()
            if not block:
                continue
            name, data = parse_sse_block(block)
            if name == "delta":
                piece = data.get("content") or ""
                if piece:
                    text_parts.append(piece)
            elif name == "done":
                done_text = data.get("content") or ""
            elif name == "error":
                had_error = True
                err = data.get("message") or "Turn failed"
                if not text_parts and not done_text:
                    done_text = err
    if done_text:
        return done_text, had_error
    return "".join(text_parts), had_error


async def _openai_stream(
    session_id: str,
    message: str,
    user_id: str,
    *,
    model: str,
    completion_id: str,
    created: int,
) -> AsyncIterator[str]:
    yield _sse_data(_chunk(completion_id, created, model, {"role": "assistant"}))
    emitted_any = False
    finish_reason = "stop"
    async for raw in run_session_turn(session_id, message, user_id, start_seq=0):
        for block in raw.split("\n\n"):
            block = block.strip()
            if not block:
                continue
            name, data = parse_sse_block(block)
            if name == "delta":
                piece = data.get("content") or ""
                if not piece:
                    continue
                emitted_any = True
                yield _sse_data(
                    _chunk(completion_id, created, model, {"content": piece})
                )
            elif name == "done":
                # Prefer incremental deltas; if none were streamed, emit done content.
                content = data.get("content") or ""
                if content and not emitted_any:
                    yield _sse_data(
                        _chunk(completion_id, created, model, {"content": content})
                    )
                    emitted_any = True
            elif name == "error":
                finish_reason = "error"
                msg = data.get("message") or "Turn failed"
                if not emitted_any:
                    yield _sse_data(
                        _chunk(completion_id, created, model, {"content": msg})
                    )
                    emitted_any = True
    yield _sse_data(_chunk(completion_id, created, model, {}, finish_reason))
    yield _sse_data("[DONE]")


@router.post("/chat/completions")
async def chat_completions(body: ChatCompletionsIn, request: Request, _: AuthDep):
    agent_id = (body.model or "").strip()
    provided_session = (request.headers.get(_SESSION_HEADER) or "").strip()

    # Solo path requires a real agent id. Session continuation only needs a
    # non-empty OpenAI ``model`` label (may be a display name).
    if not provided_session and not store.get_agent(agent_id):
        return JSONResponse(
            _openai_error(
                f"Model (agent) not found: {agent_id}",
                err_type="not_found_error",
            ),
            status_code=404,
        )

    user_text = last_user_message(body.messages)
    if not user_text:
        return JSONResponse(
            _openai_error("No user message found in messages"),
            status_code=400,
        )

    user_id = session_user_id(request)
    session_id, sess_err = resolve_session_id(
        request, agent_id=agent_id, user_id=user_id
    )
    if sess_err is not None:
        status = 404 if sess_err["error"].get("type") == "not_found_error" else 400
        return JSONResponse(sess_err, status_code=status)
    assert session_id is not None

    response_model = _response_model(agent_id, session_id)
    completion_id = _completion_id()
    created = int(time.time())
    headers = {
        _SESSION_HEADER: session_id,
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
    }

    if body.stream:
        return StreamingResponse(
            _openai_stream(
                session_id,
                user_text,
                user_id,
                model=response_model,
                completion_id=completion_id,
                created=created,
            ),
            media_type="text/event-stream",
            headers=headers,
        )

    text, had_error = await _collect_assistant_text(session_id, user_text, user_id)
    payload = {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": response_model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "error" if had_error else "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }
    return JSONResponse(payload, headers={_SESSION_HEADER: session_id})
