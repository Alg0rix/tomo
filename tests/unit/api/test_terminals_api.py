"""WebSocket teardown completes even when its ASGI cancel scope is cancelled."""

from types import SimpleNamespace

import anyio
from starlette.websockets import WebSocket

from app.api import terminals


async def test_cancelled_detach_joins_workers_and_closes_socket(monkeypatch):
    ready = anyio.Event()
    sent = []
    terminal = SimpleNamespace(
        cleanup=None, output=[], listeners=set(), exit_code=None,
        info=lambda: {'id': 'terminal'},
    )
    monkeypatch.setattr(terminals.store, 'get_owned_session', lambda *args: {'id': 'session'})
    monkeypatch.setattr(terminals.terminal_manager, 'get', lambda *args: terminal)
    connected = False

    async def receive():
        nonlocal connected
        if not connected:
            connected = True
            return {'type': 'websocket.connect'}
        await anyio.sleep_forever()

    async def send(message):
        sent.append(message)
        if message['type'] == 'websocket.send':
            ready.set()

    websocket = WebSocket({
        'type': 'websocket', 'session': {'auth': True, 'user_id': 'web'},
        'headers': [(b'host', b'testserver'), (b'origin', b'http://testserver')],
    }, receive, send)

    async def attach(*, task_status):
        with anyio.CancelScope() as scope:
            task_status.started(scope)
            await terminals.attach_terminal(websocket, 'session', 'terminal')

    with anyio.fail_after(3):
        async with anyio.create_task_group() as group:
            scope = await group.start(attach)
            await ready.wait()
            scope.cancel()
    assert not terminal.listeners
    assert sent[-1]['type'] == 'websocket.close'
