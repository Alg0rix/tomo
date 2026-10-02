"""WebSocket teardown completes even when its ASGI cancel scope is cancelled."""

from types import SimpleNamespace

import anyio
from starlette.websockets import WebSocket

from app.api import terminals


