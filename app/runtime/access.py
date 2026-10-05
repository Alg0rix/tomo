"""Explicit execution identity and immutable access ceilings.

Binding is not authorization. Resolve at ingress, revalidate before every action,
and deserialize durable job contexts only as ceilings for current policy checks.
No missing-context host or anonymous-user fallback is permitted.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import asdict, dataclass, field
from typing import Iterator


class AccessDenied(PermissionError):
    """Safe policy rejection; never includes private resource metadata."""


class AccessUnavailable(AccessDenied):
    """Required policy or execution barrier is not currently available."""


class AccessChangePending(AccessUnavailable):
    """Owner-safe recovery guidance, safe to display at HTTP ingress."""


@dataclass(frozen=True)
class ExecutionQuota:
    cpu: float = 2
    memory_mb: int = 2048
    disk_mb: int = 4096
    duration_seconds: int = 300
    max_concurrent_jobs: int = 2
    gpu_allowed: bool = False


@dataclass(frozen=True)
class ResourceAccess:
    workplace_id: str
    root_path: str
    permission: str
    destination_id: str = "local"
    kind: str = "local"
    storage_kind: str = "external"
    mount_path: str = ""
    # Cross-machine enabled resources are transfer endpoints only: they are
    # never mounted at the execution destination. Same-machine resources
    # mount normally (transfer_only=False).
    transfer_only: bool = False

    @property
    def resource_id(self) -> str:
        return self.workplace_id

    @property
    def writable(self) -> bool:
        return self.permission == "read_write"


@dataclass(frozen=True)
class ExecutionContext:
    user_id: str
    session_id: str
    agent_id: str
    role: str
    active_workplace_id: str
    resources: tuple[ResourceAccess, ...] = ()
    execution_mode: str = "restricted"
    destination_id: str = "local"
    access_generation: int = 0
    quota: ExecutionQuota = field(default_factory=ExecutionQuota)
    tool_ids: frozenset[str] = frozenset()

    @property
    def workplace_id(self) -> str:
        return self.active_workplace_id

    def to_dict(self) -> dict:
        out = asdict(self)
        out["resources"] = [asdict(resource) for resource in self.resources]
        out["tool_ids"] = sorted(self.tool_ids)
        return out

    @classmethod
    def from_dict(cls, value: dict) -> ExecutionContext:
        data = dict(value)
        data["resources"] = tuple(ResourceAccess(**r) for r in data.get("resources", ()))
        data["quota"] = ExecutionQuota(**data.get("quota", {}))
        data["tool_ids"] = frozenset(data.get("tool_ids", ()))
        return cls(**data)


_execution: ContextVar[ExecutionContext | None] = ContextVar("execution_access", default=None)


def bind_execution(context: ExecutionContext) -> Token:
    if not isinstance(context, ExecutionContext):
        raise AccessDenied("Invalid execution identity")
    return _execution.set(context)


def reset_execution(token: Token) -> None:
    # Safe across async-generator ``aclose`` / ``GeneratorExit``: ContextVar
    # tokens must be reset in the same Context that created them. When a turn
    # generator is cancelled or aclosed from another task (timeout, parent
    # cancellation, revocation drain), fall back to clearing the local value,
    # mirroring ``sandbox.reset_agent``. The creating Context is being torn
    # down, so its binding must not survive there either.
    try:
        _execution.reset(token)
    except ValueError:
        _execution.set(None)


def current_execution(required: bool = True) -> ExecutionContext | None:
    context = _execution.get()
    if context is None and required:
        raise AccessDenied("Execution identity is required")
    return context


@contextmanager
def execution_scope(context: ExecutionContext) -> Iterator[ExecutionContext]:
    token = bind_execution(context)
    try:
        yield context
    finally:
        reset_execution(token)


def revalidate_execution() -> ExecutionContext:
    from app.services.access import access

    context = current_execution()
    assert context is not None
    return access.revalidate(context)


def delegate_execution(agent_id: str) -> ExecutionContext:
    from app.services.access import access

    parent = revalidate_execution()
    return access.resolve_context(parent.user_id, parent.session_id, agent_id, parent=parent)
