"""HITL resolve API — approvals and clarify answers."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.deps import AuthDep
from app.runtime.permissions import hitl

router = APIRouter(prefix="/api", tags=["approvals"])


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
