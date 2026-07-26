"""SSH remote workplace — Connect probe (Alpha Slice D).

No paramiko dependency in Alpha: the default probe opens a TCP socket to
``ssh_host:ssh_port`` and checks credentials are present. Unit tests monkeypatch
:func:`probe_ssh` to simulate success/failure without a real host.
"""

from __future__ import annotations

import socket
from typing import Any, Callable

ProbeFn = Callable[[str, int, str, str, str], tuple[bool, str]]


def _default_probe(
    host: str, port: int, user: str, password: str, key: str
) -> tuple[bool, str]:
    """TCP reachability + credential presence. Not a full SSH handshake."""
    if not host:
        return False, "SSH workplace needs ssh_host"
    if not user:
        return False, "SSH workplace needs ssh_user"
    if not password and not key:
        return False, "SSH workplace needs a password or private key"
    try:
        with socket.create_connection((host, port), timeout=5.0):
            pass
    except OSError as exc:
        return False, f"Cannot reach {host}:{port}: {exc}"
    return True, f"SSH host reachable: {user}@{host}:{port}"


# Tests assign a mock here; production uses the TCP probe above.
probe_ssh: ProbeFn = _default_probe


def test_connection(workplace: dict[str, Any]) -> tuple[bool, str]:
    """Return ``(ok, message)`` using :data:`probe_ssh` (mockable)."""
    host = (workplace.get("ssh_host") or "").strip()
    port = int(workplace.get("ssh_port") or 22)
    user = (workplace.get("ssh_user") or "").strip()
    password = workplace.get("ssh_password") or ""
    key = workplace.get("ssh_key") or ""
    return probe_ssh(host, port, user, password, key)


__all__ = ["test_connection", "probe_ssh"]
