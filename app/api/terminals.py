"""Authenticated, session-scoped local terminal API (no connector/tunnel)."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.core.deps import AuthDep, session_user_id
from app.core.home import session_dir
from app.services import store
from app.services.terminals import terminal_manager

router = APIRouter(prefix="/api/sessions/{session_id}/terminals")


class TerminalCreate(BaseModel):
    cols: int = Field(default=80, ge=2, le=500)
    rows: int = Field(default=24, ge=2, le=200)


def _same_origin(connection: Request | WebSocket) -> bool:
    try:
        origin = urlsplit(connection.headers.get("origin", ""))
    except ValueError:
        return False
    # Compare host/port, including trusted reverse-proxy Host headers. WS and
    # HTTP schemes differ; proxy TLS termination may also change the scheme.
    return origin.scheme in {
        "http",
        "https",
    } and origin.netloc == connection.headers.get("host")


def _check_origin(request: Request) -> None:
    if request.headers.get("origin") and not _same_origin(request):
        raise HTTPException(403, "Cross-origin terminal requests are not allowed")


def _owned_session(request: Request, session_id: str) -> dict:
    # Shell access is personal even for administrators viewing shared channels.
    session = store.get_owned_session(session_id, session_user_id(request))
    if not session:
        raise HTTPException(404, "Session not found")
    return session


def _cwd(session: dict) -> Path:
    workplace = store.get_workplace(session.get("workplace_id") or "")
    if workplace and workplace.get("kind") == "local" and workplace.get("root_path"):
        path = Path(workplace["root_path"]).expanduser().resolve()
        if not path.is_dir():
            raise HTTPException(400, "Local workplace folder is unavailable")
        return path
    # A remote workplace must never route this terminal through its connector.
    return session_dir(session["id"]) / "workspace"


@router.get("")
async def list_terminals(session_id: str, request: Request, _: AuthDep):
    session = _owned_session(request, session_id)
    return {
        "terminals": terminal_manager.list(session_id),
        "cwd": str(_cwd(session)),
        "host": "local",
    }


@router.post("", status_code=201)
async def create_terminal(
    session_id: str, body: TerminalCreate, request: Request, _: AuthDep
):
    _check_origin(request)
    session = _owned_session(request, session_id)
    try:
        return terminal_manager.create(
            session_id, _cwd(session), body.cols, body.rows
        ).info()
    except NotImplementedError as exc:
        raise HTTPException(501, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(503, "Could not start local shell") from exc


@router.delete("/{terminal_id}")
async def close_terminal(
    session_id: str, terminal_id: str, request: Request, _: AuthDep
):
    _check_origin(request)
    _owned_session(request, session_id)
    if not await terminal_manager.close(session_id, terminal_id):
        raise HTTPException(404, "Terminal not found")
    return {"success": True}


@router.websocket("/{terminal_id}/ws")
async def attach_terminal(websocket: WebSocket, session_id: str, terminal_id: str):
    # Browser-only attachment: signed login cookie + strict Origin prevent
    # cross-site WebSocket hijacking. Do not accept credentials in query strings.
    if not websocket.session.get("auth") or not _same_origin(websocket):
        await websocket.close(code=4403)
        return
    uid = str(websocket.session.get("user_id") or "web")
    if not store.get_owned_session(session_id, uid):
        await websocket.close(code=4404)
        return
    terminal = terminal_manager.get(session_id, terminal_id)
    if not terminal or terminal.cleanup is not None:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    # Accept yields: closure or another attachment may have won the race.
    if (
        terminal_manager.get(session_id, terminal_id) is not terminal
        or terminal.cleanup is not None
    ):
        await websocket.close(code=4404)
        return
    if len(terminal.listeners) >= 4:
        await websocket.close(code=4429)
        return
    queue = asyncio.Queue(maxsize=128)
    # Snapshot + subscription without yielding: no gaps or duplicated live output.
    replay = b"".join(terminal.output)
    terminal.listeners.add(queue)

    async def send_output():
        await websocket.send_json({"type": "ready", **terminal.info()})
        if replay:
            await websocket.send_bytes(replay)
        if terminal.exit_code is not None:
            await websocket.send_json({"type": "exit", "exit_code": terminal.exit_code})
        while True:
            event = await queue.get()
            if isinstance(event, bytes):
                await websocket.send_bytes(event)
            else:
                await websocket.send_json(event)
                if event["type"] in {"closed", "overflow"}:
                    return

    async def receive_input():
        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict):
                await websocket.close(code=4400)
                return
            if message.get("type") == "input":
                data = message.get("data")
                if not isinstance(data, str) or len(data) > 65536:
                    await websocket.close(code=4400)
                    return
                await terminal.write(data)
            elif message.get("type") == "resize":
                cols, rows = message.get("cols"), message.get("rows")
                if (
                    type(cols) is not int
                    or type(rows) is not int
                    or not (2 <= cols <= 500 and 2 <= rows <= 200)
                ):
                    await websocket.close(code=4400)
                    return
                terminal.resize(cols, rows)

    tasks = [asyncio.create_task(send_output()), asyncio.create_task(receive_input())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (WebSocketDisconnect, RuntimeError, ValueError, OSError):
        pass
    finally:
        terminal.listeners.discard(queue)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with suppress(RuntimeError, WebSocketDisconnect):
            await websocket.close()
        # Detaching never kills a PTY; explicit close/session deletion/shutdown do.
