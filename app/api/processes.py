"""Private, session-bound background job snapshots and controls."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request

from app.core.deps import AuthDep, session_user_id
from app.services import store
from app.services.background_jobs import manager

from .terminals import _check_origin

router = APIRouter(prefix="/api/sessions/{session_id}/processes")

# Explicit projection: backend handles, destination receipts, credentials and
# internal retained-log paths must never reach a browser or shared artifact.
_PUBLIC = (
    "id",
    "session_id",
    "agent_id",
    "origin_message_id",
    "origin_call_id",
    "backend",
    "workplace_id",
    "command",
    "cwd",
    "started_at",
    "finished_at",
    "status",
    "returncode",
    "version",
    "truncated",
    "logs_expired",
    "monitoring_closed",
    "continuation_status",
    "delivery_status",
    "last_observed_at",
    "reason",
    "stop_late",
    "continuation_paused",
)
_LOG_PUBLIC = ("stdout", "stderr", "truncated", "logs_expired", "cursor", "version")


def _owner(request: Request, session_id: str) -> str:
    uid = session_user_id(request)
    if not store.get_owned_session(session_id, uid):
        raise HTTPException(404, "Session not found")
    return uid


def _job(session_id: str, job_id: str, uid: str) -> dict:
    job = manager.get_job(session_id, job_id)
    if not job or job.get("session_id") != session_id or job.get("user_id") != uid:
        raise HTTPException(404, "Process not found")
    return job


def _public(job: dict) -> dict:
    result = {key: job[key] for key in _PUBLIC if key in job}
    result["continuation_paused"] = store.background_jobs_paused(job["session_id"])
    return result


@router.get("")
async def list_processes(session_id: str, request: Request, _: AuthDep):
    uid = _owner(request, session_id)
    jobs = [
        _public(job)
        for job in manager.list_jobs(session_id)
        if job.get("user_id") == uid and job.get("session_id") == session_id
    ]
    return {"jobs": jobs}


@router.get("/{job_id}")
async def process_detail(session_id: str, job_id: str, request: Request, _: AuthDep):
    return _public(_job(session_id, job_id, _owner(request, session_id)))


@router.get("/{job_id}/logs")
async def process_logs(
    session_id: str,
    job_id: str,
    request: Request,
    _: AuthDep,
    tail: int = Query(default=65536, ge=1, le=1048576),
    cursor: int | None = Query(default=None, ge=0),
):
    _job(session_id, job_id, _owner(request, session_id))
    try:
        logs = await asyncio.to_thread(
            manager.logs, session_id, job_id, tail=tail, cursor=cursor
        )
    except ValueError as exc:
        raise HTTPException(404, "Process not found") from exc
    return {key: logs[key] for key in _LOG_PUBLIC if key in logs}


async def _mutate(request: Request, session_id: str, job_id: str, close: bool):
    _check_origin(request)
    job = _job(session_id, job_id, _owner(request, session_id))
    if close and job["status"] != "unknown":
        raise HTTPException(409, "Only an unknown process can close monitoring")
    operation = manager.close_monitoring if close else manager.stop_job
    try:
        result = await asyncio.to_thread(operation, session_id, job_id)
    except (KeyError, LookupError) as exc:
        raise HTTPException(404, "Process not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _public(result)


@router.post("/{job_id}/stop")
async def stop_process(session_id: str, job_id: str, request: Request, _: AuthDep):
    return await _mutate(request, session_id, job_id, False)


@router.post("/{job_id}/close-monitoring")
async def close_process_monitoring(
    session_id: str, job_id: str, request: Request, _: AuthDep
):
    return await _mutate(request, session_id, job_id, True)
