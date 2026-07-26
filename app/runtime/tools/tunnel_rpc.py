"""Back-compat re-export — prefer :mod:`app.runtime.tools.workplace_remote`."""

from __future__ import annotations

from app.runtime.tools.workplace_remote import (  # noqa: F401
    agent_tunnel_workplace_id,
    format_rpc_result,
    try_remote,
    try_tunnel_rpc,
)

__all__ = [
    "agent_tunnel_workplace_id",
    "format_rpc_result",
    "try_remote",
    "try_tunnel_rpc",
]
