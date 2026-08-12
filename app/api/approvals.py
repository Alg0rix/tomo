"""HITL resolve API — approvals and clarify answers."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.deps import AuthDep
from app.runtime.permissions import hitl
from app.services import store

router = APIRouter(prefix="/api", tags=["approvals"])


@router.get("/sessions/{session_id}/pending")
async def list_session_pending(session_id: str, _: AuthDep):
    """List unresolved approvals/clarifies + session todos for refresh rehydrate."""
    if not store.get_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    pending = hitl.list_pending_for_session(session_id)
    todos: list = []
    try:
        from app.runtime.tools import todo as todo_mod

        snap = todo_mod.get_store(session_id).snapshot()
        todos = list(snap.get("todos") or [])
    except Exception:
        todos = []
    return {
        "session_id": session_id,
        "approvals": pending.get("approvals") or [],
        "clarifies": pending.get("clarifies") or [],
        # Lease (lock) is held for the full background-turn lifetime.
        "active_turn": store.is_session_turn_active(session_id),
        # In-memory session plan — survives refresh while the process is up.
        "todos": todos,
    }


@router.get("/sessions/{session_id}/approval-mode")
async def get_approval_mode(session_id: str, _: AuthDep):
    from app.runtime.permissions.modes import mode_payload

    return mode_payload(session_id)


@router.put("/sessions/{session_id}/approval-mode")
async def put_approval_mode(session_id: str, body: dict, _: AuthDep):
    """Override approval mode for this session (works mid-turn).

    Switching to Auto (``off``) clears pending HITL cards so a stuck turn
    can continue without waiting for Once/Deny.
    """
    from app.runtime.permissions.modes import apply_session_mode, normalize_mode

    raw = body.get("mode") if isinstance(body, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        raise HTTPException(status_code=400, detail="mode required")
    return apply_session_mode(session_id, normalize_mode(raw))


@router.post("/approvals/{approval_id}")
async def post_approval(approval_id: str, body: dict, _: AuthDep):
    choice = body.get("choice") if isinstance(body, dict) else None
    reason = body.get("reason") if isinstance(body, dict) else None
    if not isinstance(choice, str) or not choice.strip():
        raise HTTPException(status_code=400, detail="choice required")
    try:
        hitl.resolve_approval(
            approval_id,
            choice.strip(),
            reason=reason if isinstance(reason, str) else None,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown or expired approval")
    except RuntimeError:
        raise HTTPException(status_code=409, detail="already resolved")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "id": approval_id, "choice": choice.strip().lower()}


@router.post("/clarify/{clarify_id}")
async def post_clarify(clarify_id: str, body: dict, _: AuthDep):
    answer = body.get("answer") if isinstance(body, dict) else None
    if answer is None:
        raise HTTPException(status_code=400, detail="answer required")
    if not isinstance(answer, str):
        answer = str(answer)
    try:
        hitl.resolve_clarify(clarify_id, answer)
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown or expired clarify")
    except RuntimeError:
        raise HTTPException(status_code=409, detail="already resolved")
    return {"ok": True, "id": clarify_id}


__all__ = ["router"]
