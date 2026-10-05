"""Authenticated, session-scoped local terminal API (no connector/tunnel)."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit

import anyio
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.core.deps import AuthDep, session_user_id
from app.runtime.access import AccessDenied, AccessUnavailable, ExecutionContext, execution_scope
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


def _context(uid: str, session_id: str) -> ExecutionContext:
    context = store.access.resolve_context(uid, session_id)
    # Capability mapping only; the managers enforce admission. Restricted
    # chats get held container terminals, granted+activated unrestricted
    # chats get host PTYs. Remote destinations have no terminal backend, and
    # there is never a host fallback for restricted execution.
    if context.execution_mode == "restricted":
        if any(r.kind != "local" or r.destination_id != "local" for r in context.resources):
            raise AccessUnavailable("Remote interactive terminal backend unavailable")
    elif context.execution_mode == "unrestricted":
        if any(r.kind != "local" for r in context.resources):
            raise AccessUnavailable("Remote interactive terminal backend unavailable")
    else:
        raise AccessDenied("Terminal execution identity unavailable")
    return context


def _retained_context(terminal, context: ExecutionContext) -> ExecutionContext:
    retained = getattr(terminal, "execution_context", None) or getattr(terminal, "execution", None)
    if isinstance(retained, dict):
        retained = ExecutionContext.from_dict(retained)
    if retained is None and context.legacy_admin:
        return store.access.revalidate(context)
    if not isinstance(retained, ExecutionContext) or retained.user_id != context.user_id or retained.session_id != context.session_id:
        raise AccessDenied("Terminal execution identity unavailable")
    return store.access.revalidate(retained)


@router.get("")
async def list_terminals(session_id: str, request: Request, _: AuthDep):
    session = _owned_session(request, session_id)
    context = _context(session_user_id(request), session_id)
    rows = terminal_manager.list(session_id, user_id=context.user_id)
    for row in rows:
        terminal = terminal_manager.get(session_id, row["id"], user_id=context.user_id)
        _retained_context(terminal, context)
    if context.execution_mode == "restricted":
        cwd = next(
            (r.mount_path for r in context.resources if r.workplace_id == context.active_workplace_id), "")
        backend = "container"
    else:
        cwd = str(_cwd(session))
        backend = "host"
    return {"terminals": rows, "cwd": cwd, "host": context.destination_id, "backend": backend}


@router.post("", status_code=201)
async def create_terminal(
    session_id: str, body: TerminalCreate, request: Request, _: AuthDep
):
    _check_origin(request)
    session = _owned_session(request, session_id)
    context = _context(session_user_id(request), session_id)
    try:
        if context.execution_mode == "restricted":
            # Held per-chat container environment: blocking hold off the
            # loop, loop-bound PTY construction, hold released on failure.
            from app.runtime.isolation.backend import backend as container_backend
            environment = await asyncio.to_thread(
                terminal_manager.prepare_container_environment, context)
            try:
                terminal = terminal_manager.attach_container_terminal(
                    context, environment, body.cols, body.rows)
            except BaseException:
                await asyncio.to_thread(
                    container_backend.unhold, context.session_id, environment)
                raise
            _retained_context(terminal, context)
            return terminal.info()
        # Granted, explicitly activated unrestricted destination: supervised
        # host PTY. The platform role is unchanged (Members stay Members).
        return terminal_manager.create_unrestricted(
            context, _cwd(session), body.cols, body.rows).info()
    except NotImplementedError as exc:
        raise HTTPException(501, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except AccessDenied:
        # Policy denials keep their fail-closed mapping (403/503
        # "Execution access unavailable"). PermissionError is an OSError
        # subclass; it must not be relabeled as a shell-spawn failure.
        raise
    except PermissionError as exc:
        raise HTTPException(503, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(503, "Could not start local shell") from exc


@router.delete("/{terminal_id}")
async def close_terminal(
    session_id: str, terminal_id: str, request: Request, _: AuthDep
):
    _check_origin(request)
    _owned_session(request, session_id)
    if not await terminal_manager.close(session_id, terminal_id, user_id=session_user_id(request)):
        raise HTTPException(404, "Terminal not found")
    return {"success": True}


@router.websocket("/{terminal_id}/ws")
async def attach_terminal(websocket: WebSocket, session_id: str, terminal_id: str):
    # Browser-only attachment: signed login cookie + strict Origin prevent
    # cross-site WebSocket hijacking. Do not accept credentials in query strings.
    if not websocket.session.get("auth") or not _same_origin(websocket):
        await websocket.close(code=4403)
        return
    uid = str(websocket.session.get("user_id") or "")
    user = store.get_user(uid)
    if not user or not user["enabled"] or user["role"] not in {"admin", "member"}:
        await websocket.close(code=4403)
        return
    if not store.get_owned_session(session_id, uid):
        await websocket.close(code=4404)
        return
    try:
        terminal = terminal_manager.get(session_id, terminal_id, user_id=uid)
    except AccessDenied:
        await websocket.close(code=4403)
        return
    if not terminal or terminal.cleanup is not None:
        await websocket.close(code=4404)
        return
    try:
        context = _retained_context(terminal, _context(uid, session_id))
    except (AccessDenied, ValueError, TypeError):
        await websocket.close(code=4403)
        return
    await websocket.accept()
    # Accept yields: closure or another attachment may have won the race.
    if (
        terminal_manager.get(session_id, terminal_id, user_id=uid) is not terminal
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
            store.access.revalidate(context)
            if isinstance(event, bytes):
                await websocket.send_bytes(event)
            else:
                await websocket.send_json(event)
                if event["type"] in {"closed", "overflow"}:
                    return

    async def receive_input():
        while True:
            message = await websocket.receive_json()
            store.access.revalidate(context)
            if not isinstance(message, dict):
                await websocket.close(code=4400)
                return
            if message.get("type") == "input":
                data = message.get("data")
                if not isinstance(data, str) or len(data) > 65536:
                    await websocket.close(code=4400)
                    return
                with execution_scope(store.access.revalidate(context)):
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
                with execution_scope(store.access.revalidate(context)):
                    terminal.resize(cols, rows)

    tasks = [asyncio.create_task(send_output()), asyncio.create_task(receive_input())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (WebSocketDisconnect, RuntimeError, ValueError, OSError, AccessDenied):
        pass
    finally:
        terminal.listeners.discard(queue)
        for task in tasks:
            task.cancel()
        # ASGI disconnect/shutdown can cancel this scope again at every await.
        # Finish joining both workers before allowing that cancellation through.
        with anyio.CancelScope(shield=True):
            await asyncio.gather(*tasks, return_exceptions=True)
            with suppress(RuntimeError, WebSocketDisconnect):
                await websocket.close()
        # Detaching never kills a PTY; explicit close/session deletion/shutdown do.
