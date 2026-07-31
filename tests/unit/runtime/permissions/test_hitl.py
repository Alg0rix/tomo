"""HITL waiter unit tests."""

from __future__ import annotations

import asyncio

import pytest

from app.runtime.permissions import hitl
from app.runtime.permissions.types import Finding


@pytest.fixture(autouse=True)
def _clean() -> None:
    hitl.clear_all_pending()
    yield
    hitl.clear_all_pending()


@pytest.mark.asyncio
async def test_approval_resolve_once() -> None:
    payload = hitl.create_approval(
        tool="bash",
        args={"command": "ls ~/.tomo"},
        findings=[
            Finding(kind="escape", key="escape:~/.tomo", description="escape")
        ],
        description="escape",
        session_id="s1",
    )

    async def _resolve() -> None:
        await asyncio.sleep(0.05)
        hitl.resolve_approval(payload["id"], "once")

    asyncio.create_task(_resolve())
    choice = await hitl.await_approval(payload["id"], timeout=2.0)
    assert choice == "once"


@pytest.mark.asyncio
async def test_approval_timeout_deny() -> None:
    payload = hitl.create_approval(
        tool="bash",
        args={"command": "rm -rf ./x"},
        findings=[],
        description="dangerous",
        session_id="s1",
    )
    choice = await hitl.await_approval(payload["id"], timeout=0.1)
    assert choice == "deny"


@pytest.mark.asyncio
async def test_clarify_resolve() -> None:
    payload = hitl.create_clarify(
        question="Which env?",
        choices=["dev", "prod"],
        session_id="s1",
    )

    async def _resolve() -> None:
        await asyncio.sleep(0.05)
        hitl.resolve_clarify(payload["id"], "dev")

    asyncio.create_task(_resolve())
    answer = await hitl.await_clarify(payload["id"], timeout=2.0)
    assert answer == "dev"
