"""Admin-only history and SSE stream of application logs."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.deps import can_manage_telegram, require_auth
from app.core.log_reader import LogReader

router = APIRouter(prefix="/api/logs", tags=["logs"])


def require_log_admin(request: Request):
    require_auth(request)
    if not can_manage_telegram(request):
        raise HTTPException(status_code=403, detail="Runtime logs require an administrator")


LogAdmin = Annotated[None, Depends(require_log_admin)]


def log_filters(
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] | None = None,
    type: Annotated[str | None, Query(max_length=160)] = None,
    session: Annotated[str | None, Query(max_length=160)] = None,
    request_id: Annotated[str | None, Query(max_length=160)] = None,
    event: Annotated[str | None, Query(max_length=80)] = None,
    tail: Annotated[int, Query(ge=0, le=500)] = 100,
):
    return dict(level=level, log_type=type or None, session=session or None,
                request_id=request_id or None, event=event or None, tail=tail)


async def reader_io(fn):
    # A cancelled request must not close the file while its worker is reading.
    task = asyncio.create_task(asyncio.to_thread(fn))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


@router.get("")
async def log_history(_: LogAdmin, filters: Annotated[dict, Depends(log_filters)]):
    reader = LogReader(**filters)
    try:
        return JSONResponse({"records": await reader_io(reader.history)}, headers={"Cache-Control": "no-store"})
    finally:
        reader.close()


@router.get("/stream")
async def log_stream(request: Request, _: LogAdmin, filters: Annotated[dict, Depends(log_filters)]):
    async def events():
        reader = LogReader(**filters)
        try:
            history = await reader_io(reader.history)
            yield "event: snapshot\ndata: " + json.dumps(history, ensure_ascii=False) + "\n\n"
            last_ping = time.monotonic()
            while not await request.is_disconnected():
                # Recheck long-lived access (disabled accounts / role changes).
                # A revoked account raises instead of returning False; either
                # way the stream must end with 'forbidden', not a bare drop.
                try:
                    allowed = can_manage_telegram(request)
                except HTTPException:
                    allowed = False
                if not allowed:
                    yield 'event: forbidden\ndata: {}\n\n'
                    return
                batch = await reader_io(reader.poll)
                for item in batch:
                    yield f"id: {item['id']}\nevent: log\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
                if time.monotonic() - last_ping >= 10:
                    yield ": keepalive\n\n"
                    last_ping = time.monotonic()
                await asyncio.sleep(0.5)
        except OSError:
            yield 'event: unavailable\ndata: {}\n\n'
        finally:
            reader.close()

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-store", "X-Accel-Buffering": "no",
    })
